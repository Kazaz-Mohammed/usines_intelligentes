"""
Service principal d'orchestration pour la prédiction RUL
"""
import logging
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone

from app.config import settings
from app.services.lstm_service import LSTMService
from app.services.gru_service import GRUService
from app.services.tcn_service import TCNService

# XGBoost optionnel
try:
    from app.services.xgboost_service import XGBoostService
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False
    XGBoostService = None

from app.models.rul_data import RULPredictionRequest, RULPredictionResult

logger = logging.getLogger(__name__)


class RULPredictionService:
    """Service principal pour orchestrer les modèles de prédiction RUL"""
    
    def __init__(self):
        """Initialise le service avec tous les modèles"""
        self.models: Dict[str, Any] = {}
        self.calibration_service = CalibrationService()
        self.transfer_learning_service = TransferLearningService()
        
        # Initialiser les modèles selon la configuration
        if settings.enable_lstm:
            try:
                lstm_service = LSTMService()
                # Appliquer transfer learning si disponible
                if (settings.transfer_learning_enabled and 
                    self.transfer_learning_service.load_pretrained_model("lstm") is not None):
                    # Le transfer learning sera appliqué lors de l'entraînement
                    logger.info("Transfer learning disponible pour LSTM")
                self.models["lstm"] = lstm_service
                logger.info("LSTM Service initialisé")
            except Exception as e:
                logger.warning(f"Erreur lors de l'initialisation LSTM: {e}")
        
        if settings.enable_gru:
            try:
                if settings.transfer_learning_enabled:
                    self.transfer_learning_service.load_pretrained_model("gru")
                gru_service = GRUService(transfer_learning_service=self.transfer_learning_service)
                self.models["gru"] = gru_service
                logger.info("GRU Service initialisé")
            except Exception as e:
                logger.warning(f"Erreur lors de l'initialisation GRU: {e}")
        
        if settings.enable_tcn:
            try:
                if settings.transfer_learning_enabled:
                    self.transfer_learning_service.load_pretrained_model("tcn")
                tcn_service = TCNService(transfer_learning_service=self.transfer_learning_service)
                self.models["tcn"] = tcn_service
                logger.info("TCN Service initialisé")
            except Exception as e:
                logger.warning(f"Erreur lors de l'initialisation TCN: {e}")
        
        if settings.enable_xgboost and XGBOOST_AVAILABLE:
            try:
                self.models["xgboost"] = XGBoostService()
                logger.info("XGBoost Service initialisé")
            except Exception as e:
                logger.warning(f"Erreur lors de l'initialisation XGBoost: {e}")
        elif settings.enable_xgboost and not XGBOOST_AVAILABLE:
            logger.warning("XGBoost demandé mais non disponible (module non installé)")
        
        logger.info(f"Service RUL Prediction initialisé avec {len(self.models)} modèles")
    
    def train_all_models(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_val: Optional[np.ndarray] = None,
        y_val: Optional[np.ndarray] = None,
        feature_names: Optional[List[str]] = None,
        epochs: Optional[int] = None,
        batch_size: Optional[int] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Entraîne tous les modèles activés
        
        Args:
            X_train: Données d'entraînement
            y_train: Targets d'entraînement
            X_val: Données de validation (optionnel)
            y_val: Targets de validation (optionnel)
            feature_names: Noms des features
            epochs: Nombre d'epochs (override config)
            batch_size: Taille de batch (override config)
        
        Returns:
            Dict avec métriques de chaque modèle
        """
        logger.info(f"Début de l'entraînement de {len(self.models)} modèles")
        
        results = {}
        
        for model_name, model_service in self.models.items():
            try:
                logger.info(f"Entraînement du modèle {model_name}...")
                
                # Entraîner le modèle
                if hasattr(model_service, 'train'):
                    # Appliquer transfer learning si disponible
                    if (settings.transfer_learning_enabled and 
                        model_name in self.transfer_learning_service.pretrained_models):
                        # Le transfer learning sera appliqué dans la méthode train du service
                        logger.info(f"Transfer learning sera appliqué pour {model_name}")
                    
                    # Passer epochs et batch_size si disponibles
                    train_kwargs = {}
                    if epochs is not None and 'epochs' in model_service.train.__code__.co_varnames:
                        train_kwargs['epochs'] = epochs
                    if batch_size is not None and 'batch_size' in model_service.train.__code__.co_varnames:
                        train_kwargs['batch_size'] = batch_size
                    
                    metrics = model_service.train(
                        X_train,
                        y_train,
                        X_val=X_val,
                        y_val=y_val,
                        feature_names=feature_names,
                        **train_kwargs
                    )
                    
                    results[model_name] = {
                        "status": "success",
                        "metrics": metrics
                    }
                    logger.info(f"Modèle {model_name} entraîné avec succès")
                else:
                    results[model_name] = {
                        "status": "error",
                        "error": "Méthode train() non disponible"
                    }
                    
            except Exception as e:
                logger.error(f"Erreur lors de l'entraînement de {model_name}: {e}", exc_info=True)
                results[model_name] = {
                    "status": "error",
                    "error": str(e)
                }
        
        logger.info(f"Entraînement terminé. {sum(1 for r in results.values() if r.get('status') == 'success')}/{len(results)} modèles entraînés")
        
        return results
    
    def predict_rul(
        self,
        request: RULPredictionRequest,
        model_name: Optional[str] = None,
        use_ensemble: bool = True
    ) -> RULPredictionResult:
        """
        Prédit la RUL pour un actif
        
        Args:
            request: Requête de prédiction
            model_name: Nom du modèle à utiliser (si None, utilise ensemble)
            use_ensemble: Si True, agrège les prédictions de tous les modèles
        
        Returns:
            Résultat de prédiction avec intervalle de confiance
        """
        if not self.is_ready():
            raise RuntimeError("Aucun modèle n'est entraîné. Appelez train_all_models() d'abord.")
        
        # Convertir features en array numpy
        feature_values = list(request.features.values())
        feature_array = np.array(feature_values).reshape(1, -1)
        
        # Si sequence_data fourni, l'utiliser
        if request.sequence_data:
            sequence_array = np.array([
                list(seq.values()) for seq in request.sequence_data
            ])
        else:
            # Créer une séquence à partir des features actuelles
            sequence_array = feature_array
        
        predictions = {}
        model_scores = {}
        
        # Prédictions avec chaque modèle entraîné
        trained_models = {name: model for name, model in self.models.items() if self._is_model_trained(model)}
        
        if not trained_models:
            raise RuntimeError("Aucun modèle entraîné disponible")
        
        # Si model_name spécifié, utiliser seulement ce modèle
        if model_name and model_name in trained_models:
            models_to_use = {model_name: trained_models[model_name]}
        elif model_name:
            raise ValueError(f"Modèle {model_name} non disponible ou non entraîné")
        else:
            models_to_use = trained_models
        
        for name, model in models_to_use.items():
            try:
                pred = model.predict(sequence_array)
                predictions[name] = float(pred[0]) if len(pred) > 0 else 0.0
                model_scores[name] = predictions[name]
            except Exception as e:
                logger.warning(f"Erreur lors de la prédiction avec {name}: {e}")
                continue
        
        if not predictions:
            raise RuntimeError("Aucune prédiction réussie")
        
        # Agrégation des prédictions
        if use_ensemble and len(predictions) > 1:
            # Moyenne pondérée (poids égaux pour l'instant)
            predictions_array = np.array(list(predictions.values()))
            final_rul = float(np.mean(predictions_array))
            
            # Calcul de l'incertitude avec le service de calibration
            uncertainty, confidence_interval_lower, confidence_interval_upper = \
                self.calibration_service.compute_uncertainty(
                    predictions_array.reshape(-1, 1),
                    method="std"
                )
            uncertainty = float(uncertainty[0])
            confidence_interval_lower = float(confidence_interval_lower[0])
            confidence_interval_upper = float(confidence_interval_upper[0])
            
            # Appliquer calibration si disponible
            if self.calibration_service.is_calibrated:
                calibrated_rul = self.calibration_service.calibrate_predictions(
                    np.array([final_rul])
                )
                final_rul = float(calibrated_rul[0])
                # Recalculer l'intervalle de confiance avec la prédiction calibrée
                confidence_interval_lower, confidence_interval_upper = \
                    self.calibration_service.compute_confidence_interval(
                        final_rul, uncertainty, confidence_level=0.95
                    )
            
            confidence_level = 0.95
            model_used = "ensemble"
        else:
            # Utiliser la première prédiction disponible
            model_used = list(predictions.keys())[0]
            final_rul = predictions[model_used]
            
            # Appliquer calibration si disponible
            if self.calibration_service.is_calibrated:
                calibrated_rul = self.calibration_service.calibrate_predictions(
                    np.array([final_rul])
                )
                final_rul = float(calibrated_rul[0])
            
            # Calculer l'incertitude
            uncertainty, confidence_interval_lower, confidence_interval_upper = \
                self.calibration_service.compute_uncertainty(
                    np.array([final_rul]),
                    method="std"
                )
            uncertainty = float(uncertainty[0])
            confidence_interval_lower = float(confidence_interval_lower[0])
            confidence_interval_upper = float(confidence_interval_upper[0])
            
            confidence_level = 0.95
        
        # S'assurer que RUL est positive
        final_rul = max(0.0, final_rul)
        confidence_interval_lower = max(0.0, confidence_interval_lower)
        confidence_interval_upper = max(0.0, confidence_interval_upper)
        
        return RULPredictionResult(
            asset_id=request.asset_id,
            sensor_id=request.sensor_id,
            timestamp=request.timestamp or datetime.now(timezone.utc),
            rul_prediction=float(final_rul),
            confidence_interval_lower=float(confidence_interval_lower),
            confidence_interval_upper=float(confidence_interval_upper),
            confidence_level=confidence_level,
            uncertainty=float(uncertainty),
            model_used=model_used,
            model_scores=model_scores,
            features=request.features,
            metadata=request.metadata or {}
        )
    
    def predict_rul_batch(
        self,
        requests: List[RULPredictionRequest],
        model_name: Optional[str] = None,
        use_ensemble: bool = True
    ) -> List[RULPredictionResult]:
        """
        Prédit la RUL pour plusieurs actifs (batch)
        
        Args:
            requests: Liste de requêtes
            model_name: Nom du modèle à utiliser
            use_ensemble: Si True, agrège les prédictions
        
        Returns:
            Liste des résultats de prédiction
        """
        results = []
        
        for request in requests:
            try:
                result = self.predict_rul(request, model_name, use_ensemble)
                results.append(result)
            except Exception as e:
                logger.error(f"Erreur lors de la prédiction pour {request.asset_id}: {e}", exc_info=True)
                # Créer un résultat d'erreur
                results.append(RULPredictionResult(
                    asset_id=request.asset_id,
                    sensor_id=request.sensor_id,
                    timestamp=request.timestamp or datetime.now(timezone.utc),
                    rul_prediction=0.0,
                    confidence_interval_lower=0.0,
                    confidence_interval_upper=0.0,
                    confidence_level=0.95,
                    uncertainty=0.0,
                    model_used="error",
                    model_scores={},
                    features=request.features,
                    metadata={"error": str(e)}
                ))
        
        return results
    
    def _is_model_trained(self, model: Any) -> bool:
        """Vérifie si un modèle est entraîné"""
        return hasattr(model, 'is_trained') and model.is_trained
    
    def get_model_status(self) -> Dict[str, Dict[str, Any]]:
        """
        Retourne le statut de tous les modèles
        
        Returns:
            Dict avec statut de chaque modèle
        """
        status = {}
        
        for name, model in self.models.items():
            try:
                if hasattr(model, 'get_model_info'):
                    info = model.get_model_info()
                    status[name] = {
                        "available": True,
                        "trained": self._is_model_trained(model),
                        **info
                    }
                else:
                    status[name] = {
                        "available": True,
                        "trained": self._is_model_trained(model)
                    }
            except Exception as e:
                status[name] = {
                    "available": False,
                    "error": str(e)
                }
        
        return status
    
    def is_ready(self) -> bool:
        """
        Vérifie si au moins un modèle est entraîné et prêt
        
        Returns:
            True si au moins un modèle est prêt
        """
        return any(self._is_model_trained(model) for model in self.models.values())
    
    def get_best_model(self) -> Optional[str]:
        """
        Retourne le nom du meilleur modèle basé sur les métriques
        
        Returns:
            Nom du meilleur modèle ou None
        """
        # Pour l'instant, retourner le premier modèle entraîné
        # Plus tard, on pourra utiliser les métriques de validation
        for name, model in self.models.items():
            if self._is_model_trained(model):
                return name
        return None

