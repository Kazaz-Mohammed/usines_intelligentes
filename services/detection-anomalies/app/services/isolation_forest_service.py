"""
Service Isolation Forest pour la détection d'anomalies
"""
import logging
import numpy as np
from typing import Dict, List, Optional, Any
from pyod.models.iforest import IForest
from app.config import settings
from app.services.mlflow_service import MLflowService

logger = logging.getLogger(__name__)


class IsolationForestService:
    """Service pour la détection d'anomalies avec Isolation Forest"""
    
    def __init__(self):
        """Initialise le service Isolation Forest"""
        self.model: Optional[IForest] = None
        self.feature_names: Optional[List[str]] = None
        self.is_trained: bool = False
        self.mlflow_service = MLflowService()
        
        # Paramètres depuis la config
        self.contamination = settings.isolation_forest_contamination
        self.n_estimators = settings.isolation_forest_n_estimators
        self.max_samples = settings.isolation_forest_max_samples
        
        logger.info(
            f"IsolationForestService initialisé avec contamination={self.contamination}, "
            f"n_estimators={self.n_estimators}, max_samples={self.max_samples}"
        )
    
    def train(self, X: np.ndarray, feature_names: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Entraîne le modèle Isolation Forest
        
        Args:
            X: Données d'entraînement (n_samples, n_features)
            feature_names: Noms des features (optionnel)
        
        Returns:
            Dict avec les métriques d'entraînement
        """
        try:
            logger.info(f"Entraînement Isolation Forest sur {X.shape[0]} échantillons, {X.shape[1]} features")
            
            # Initialiser le modèle
            self.model = IForest(
                contamination=self.contamination,
                n_estimators=self.n_estimators,
                max_samples=self.max_samples,
                random_state=42,
                n_jobs=-1
            )
            
            # Entraîner
            self.model.fit(X)
            
            # Sauvegarder les noms de features
            if feature_names is not None:
                self.feature_names = feature_names
            else:
                self.feature_names = [f"feature_{i}" for i in range(X.shape[1])]
            
            self.is_trained = True
            
            # Calculer quelques métriques sur les données d'entraînement
            train_scores = self.model.decision_scores_
            train_predictions = self.model.labels_
            
            n_anomalies = np.sum(train_predictions == 1)
            anomaly_rate = n_anomalies / len(train_predictions)
            
            metrics = {
                "n_samples": X.shape[0],
                "n_features": X.shape[1],
                "n_anomalies_detected": int(n_anomalies),
                "anomaly_rate": float(anomaly_rate),
                "mean_score": float(np.mean(train_scores)),
                "std_score": float(np.std(train_scores)),
                "min_score": float(np.min(train_scores)),
                "max_score": float(np.max(train_scores))
            }
            
            # Logging MLflow
            run = self.mlflow_service.start_run(run_name=f"isolation_forest_{X.shape[0]}_samples")
            try:
                # Log parameters
                self.mlflow_service.log_params({
                    "model_type": "isolation_forest",
                    "contamination": self.contamination,
                    "n_estimators": self.n_estimators,
                    "max_samples": str(self.max_samples),
                    "n_samples": X.shape[0],
                    "n_features": X.shape[1]
                })
                
                # Log metrics
                self.mlflow_service.log_metrics({
                    "n_anomalies_detected": metrics["n_anomalies_detected"],
                    "anomaly_rate": metrics["anomaly_rate"],
                    "mean_score": metrics["mean_score"],
                    "std_score": metrics["std_score"]
                })
                
                # Log model
                self.mlflow_service.log_model_sklearn(
                    self.model,
                    artifact_path="isolation_forest_model",
                    registered_model_name="isolation_forest"
                )
            finally:
                if run:
                    self.mlflow_service.end_run()
            
            logger.info(f"Isolation Forest entraîné avec succès. Métriques: {metrics}")
            
            return metrics
            
        except Exception as e:
            logger.error(f"Erreur lors de l'entraînement Isolation Forest: {e}", exc_info=True)
            raise
    
    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        Prédit les labels d'anomalie (0=normal, 1=anomalie)
        
        Args:
            X: Données à prédire (n_samples, n_features)
        
        Returns:
            Labels (0 ou 1)
        """
        if not self.is_trained or self.model is None:
            raise ValueError("Le modèle n'a pas été entraîné. Appelez train() d'abord.")
        
        try:
            predictions = self.model.predict(X)
            return predictions
        except Exception as e:
            logger.error(f"Erreur lors de la prédiction Isolation Forest: {e}", exc_info=True)
            raise
    
    def predict_scores(self, X: np.ndarray) -> np.ndarray:
        """
        Prédit les scores d'anomalie (plus élevé = plus anormal)
        
        Args:
            X: Données à scorer (n_samples, n_features)
        
        Returns:
            Scores d'anomalie
        """
        if not self.is_trained or self.model is None:
            raise ValueError("Le modèle n'a pas été entraîné. Appelez train() d'abord.")
        
        try:
            scores = self.model.decision_function(X)
            # Normaliser les scores entre 0 et 1
            # Isolation Forest retourne des scores négatifs pour les anomalies
            # On inverse et normalise
            scores_normalized = (scores - scores.min()) / (scores.max() - scores.min() + 1e-10)
            return scores_normalized
        except Exception as e:
            logger.error(f"Erreur lors du scoring Isolation Forest: {e}", exc_info=True)
            raise
    
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """
        Prédit les probabilités d'anomalie (0=normal, 1=anomalie)
        
        Args:
            X: Données à scorer (n_samples, n_features)
        
        Returns:
            Probabilités [prob_normal, prob_anomaly] pour chaque échantillon
        """
        if not self.is_trained or self.model is None:
            raise ValueError("Le modèle n'a pas été entraîné. Appelez train() d'abord.")
        
        try:
            scores = self.model.decision_function(X)
            # Normaliser les scores entre 0 et 1
            scores_normalized = (scores - scores.min()) / (scores.max() - scores.min() + 1e-10)
            
            # Probabilité d'anomalie = score normalisé
            prob_anomaly = scores_normalized
            prob_normal = 1 - prob_anomaly
            
            # Retourner [prob_normal, prob_anomaly]
            return np.column_stack([prob_normal, prob_anomaly])
        except Exception as e:
            logger.error(f"Erreur lors du calcul de probabilité Isolation Forest: {e}", exc_info=True)
            raise
    
    def detect_anomaly(self, features: Dict[str, float], threshold: Optional[float] = None) -> Dict[str, Any]:
        """
        Détecte une anomalie à partir d'un dictionnaire de features
        
        Args:
            features: Dictionnaire {nom_feature: valeur}
            threshold: Seuil personnalisé (optionnel, utilise contamination par défaut)
        
        Returns:
            Dict avec score, is_anomaly, etc.
        """
        if not self.is_trained or self.model is None:
            raise ValueError("Le modèle n'a pas été entraîné. Appelez train() d'abord.")
        
        try:
            # Convertir le dictionnaire en array numpy
            if self.feature_names is None:
                raise ValueError("Les noms de features ne sont pas définis")
            
            # Créer un array avec les features dans le bon ordre
            X = np.array([[features.get(name, 0.0) for name in self.feature_names]])
            
            # Prédire
            score = self.predict_scores(X)[0]
            prediction = self.predict(X)[0]
            
            # Utiliser le seuil fourni ou celui du modèle
            if threshold is None:
                threshold = self.contamination
            
            is_anomaly = score >= threshold or prediction == 1
            
            return {
                "score": float(score),
                "is_anomaly": bool(is_anomaly),
                "threshold": float(threshold),
                "prediction": int(prediction)
            }
            
        except Exception as e:
            logger.error(f"Erreur lors de la détection d'anomalie: {e}", exc_info=True)
            raise
    
    def get_model_info(self) -> Dict[str, Any]:
        """Retourne les informations sur le modèle"""
        return {
            "model_type": "isolation_forest",
            "is_trained": self.is_trained,
            "contamination": self.contamination,
            "n_estimators": self.n_estimators,
            "max_samples": self.max_samples,
            "n_features": len(self.feature_names) if self.feature_names else 0,
            "feature_names": self.feature_names
        }

