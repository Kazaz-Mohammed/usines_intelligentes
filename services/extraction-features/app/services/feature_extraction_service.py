"""
Service principal d'orchestration de l'extraction de features
"""
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime
from collections import defaultdict
import uuid

from app.models.feature_data import (
    PreprocessedDataReference,
    WindowedDataReference,
    ExtractedFeature,
    ExtractedFeaturesVector
)
from app.services.kafka_consumer import KafkaConsumerService
from app.services.kafka_producer import KafkaProducerService
from app.services.temporal_features_service import TemporalFeaturesService
from app.services.tsfresh_features_service import TSFreshFeaturesService
from app.services.frequency_features_service import FrequencyFeaturesService
from app.services.wavelet_features_service import WaveletFeaturesService
from app.services.standardization_service import StandardizationService
from app.services.feast_service import FeastService
from app.services.asset_service import AssetService
from app.database.timescaledb import TimescaleDBService
from app.config import settings

logger = logging.getLogger(__name__)


class FeatureExtractionService:
    """
    Orchestre l'extraction de features depuis les données prétraitées.
    Peut fonctionner en mode streaming (traitement immédiat)
    ou en mode batch (traitement par fenêtres).
    """
    
    def __init__(self):
        self.kafka_consumer = KafkaConsumerService()
        self.kafka_producer = KafkaProducerService()
        self.temporal_features_service = TemporalFeaturesService()
        self.tsfresh_features_service = TSFreshFeaturesService()
        self.frequency_features_service = FrequencyFeaturesService()
        self.wavelet_features_service = WaveletFeaturesService()
        self.standardization_service = StandardizationService()
        self.feast_service = FeastService()
        self.asset_service = AssetService()
        self.timescale_db_service = TimescaleDBService()
        
        # Buffer pour les données par asset
        self.data_buffer: Dict[str, List[ExtractedFeature]] = defaultdict(list)
        self.last_processed_time: Dict[str, datetime] = defaultdict(lambda: datetime.min)
    
    async def process_preprocessed_data(
        self,
        preprocessed_data: List[PreprocessedDataReference],
        mode: str = "streaming"
    ):
        """
        Traite des données prétraitées et extrait les features
        
        Args:
            preprocessed_data: Liste de données prétraitées
            mode: "streaming" pour traitement immédiat, "batch" pour accumulation
        """
        if not preprocessed_data:
            return
        
        # Grouper par asset_id et sensor_id
        grouped_by_asset: Dict[str, Dict[str, List[PreprocessedDataReference]]] = defaultdict(lambda: defaultdict(list))
        
        for data in preprocessed_data:
            grouped_by_asset[data.asset_id][data.sensor_id].append(data)
        
        # Traiter chaque asset
        for asset_id, sensors_data in grouped_by_asset.items():
            try:
                # Obtenir le type d'actif (depuis la base de données ou les métadonnées)
                asset_type = await self._get_asset_type(asset_id)
                
                # Calculer les features pour chaque capteur
                all_features: List[ExtractedFeature] = []
                
                for sensor_id, sensor_data in sensors_data.items():
                    # Trier par timestamp
                    sensor_data.sort(key=lambda x: x.timestamp)
                    
                    # Calculer les features temporelles
                    if settings.enable_temporal_features:
                        temporal_features = self.temporal_features_service.calculate_temporal_features(
                            sensor_data
                        )
                        all_features.extend(temporal_features)
                        
                        # Calculer les features tsfresh (optionnel)
                        if self.tsfresh_features_service.is_available():
                            try:
                                tsfresh_features = self.tsfresh_features_service.calculate_tsfresh_features(
                                    sensor_data
                                )
                                all_features.extend(tsfresh_features)
                            except Exception as e:
                                logger.warning(f"Erreur lors du calcul des features tsfresh: {e}")
                    
                    # Calculer les features fréquentielles
                    if settings.enable_frequency_features:
                        frequency_features = self.frequency_features_service.calculate_frequency_features(
                            sensor_data
                        )
                        all_features.extend(frequency_features)
                        
                        # Calculer l'énergie par bande
                        try:
                            band_energy_features = self.frequency_features_service.calculate_band_energy(
                                sensor_data
                            )
                            all_features.extend(band_energy_features)
                        except Exception as e:
                            logger.warning(f"Erreur lors du calcul de l'énergie de bande: {e}")
                    
                    # Calculer les features ondelettes
                    if settings.enable_wavelet_features and self.wavelet_features_service.is_available():
                        try:
                            wavelet_features = self.wavelet_features_service.calculate_wavelet_features(
                                sensor_data
                            )
                            all_features.extend(wavelet_features)
                        except Exception as e:
                            logger.warning(f"Erreur lors du calcul des features ondelettes: {e}")
                
                # Standardiser les features par type d'actif
                if settings.enable_standardization and all_features:
                    standardized_features = self.standardization_service.standardize_features(
                        all_features,
                        asset_type
                    )
                    all_features.extend(standardized_features)
                
                # Stocker dans TimescaleDB
                if all_features:
                    self.timescale_db_service.insert_extracted_features_batch(all_features)
                    logger.debug(f"Features stockées dans TimescaleDB pour asset={asset_id}: {len(all_features)} features")
                
                # Stocker dans Feast (si activé)
                if self.feast_service.is_available() and all_features:
                    # Grouper les features par timestamp pour Feast
                    features_by_timestamp = defaultdict(list)
                    for feature in all_features:
                        features_by_timestamp[feature.timestamp].append(feature)
                    
                    for timestamp, features in features_by_timestamp.items():
                        self.feast_service.store_features(features, asset_id, timestamp)
                    
                    logger.debug(f"Features stockées dans Feast pour asset={asset_id}: {len(all_features)} features")
                
                # Publier sur Kafka
                if mode == "streaming":
                    # Mode streaming: publier immédiatement
                    await self.kafka_producer.publish_extracted_features_batch(all_features)
                    logger.info(f"Publié {len(all_features)} features en streaming pour asset={asset_id}")
                elif mode == "batch":
                    # Mode batch: accumuler et traiter par fenêtres
                    self._accumulate_data(asset_id, all_features)
                    feature_vector = await self._process_and_publish_batch(asset_id, asset_type)
                    if feature_vector:
                        self.timescale_db_service.insert_feature_vector(feature_vector)
                        logger.debug(f"Vecteur de features stocké dans TimescaleDB pour asset={asset_id}")
                
            except Exception as e:
                logger.error(f"Erreur lors du traitement des données pour asset={asset_id}: {e}", exc_info=True)
                continue
    
    async def process_windowed_data(
        self,
        windowed_data: WindowedDataReference
    ):
        """
        Traite des fenêtres de données et extrait les features
        
        Args:
            windowed_data: Fenêtre de données prétraitées
        """
        try:
            # Obtenir le type d'actif
            asset_type = await self._get_asset_type(windowed_data.asset_id)
            
            # Calculer les features pour chaque capteur
            all_features: List[ExtractedFeature] = []
            
            for sensor_id, sensor_data in windowed_data.sensor_data.items():
                # Trier par timestamp
                sensor_data.sort(key=lambda x: x.timestamp)
                
                # Calculer les features temporelles
                if settings.enable_temporal_features:
                    temporal_features = self.temporal_features_service.calculate_temporal_features(
                        sensor_data
                    )
                    all_features.extend(temporal_features)
                
                # Calculer les features fréquentielles
                if settings.enable_frequency_features:
                    frequency_features = self.frequency_features_service.calculate_frequency_features(
                        sensor_data
                    )
                    all_features.extend(frequency_features)
                
                # Calculer les features ondelettes
                if settings.enable_wavelet_features and self.wavelet_features_service.is_available():
                    wavelet_features = self.wavelet_features_service.calculate_wavelet_features(
                        sensor_data
                    )
                    all_features.extend(wavelet_features)
            
            # Créer un vecteur de features
            feature_vector = self._create_feature_vector(
                windowed_data,
                all_features,
                asset_type
            )
            
            # Standardiser le vecteur de features
            if settings.enable_standardization:
                feature_vector = self.standardization_service.standardize_feature_vector(
                    feature_vector,
                    asset_type
                )
            
            # Stocker dans TimescaleDB
            self.timescale_db_service.insert_feature_vector(feature_vector)
            logger.debug(f"Vecteur de features stocké dans TimescaleDB: {feature_vector.feature_vector_id}")
            
            # Stocker dans Feast (si activé)
            if self.feast_service.is_available():
                self.feast_service.store_feature_vector(feature_vector, windowed_data.asset_id)
                logger.debug(f"Vecteur de features stocké dans Feast: {feature_vector.feature_vector_id}")
            
            # Publier sur Kafka
            self.kafka_producer.publish_feature_vector(feature_vector)
            logger.info(f"Vecteur de features publié: {feature_vector.feature_vector_id}")
            
        except Exception as e:
            logger.error(f"Erreur lors du traitement de la fenêtre {windowed_data.window_id}: {e}", exc_info=True)
            raise
    
    def _create_feature_vector(
        self,
        windowed_data: WindowedDataReference,
        features: List[ExtractedFeature],
        asset_type: Optional[str] = None
    ) -> ExtractedFeaturesVector:
        """
        Crée un vecteur de features à partir d'une liste de features
        
        Args:
            windowed_data: Fenêtre de données
            features: Liste de features
            asset_type: Type d'actif
        
        Returns:
            Vecteur de features
        """
        # Convertir les features en dictionnaire
        features_dict = {feature.feature_name: feature.feature_value for feature in features}
        
        # Créer le vecteur de features
        feature_vector = ExtractedFeaturesVector(
            feature_vector_id=f"fv_{windowed_data.window_id}",
            timestamp=windowed_data.end_time,
            asset_id=windowed_data.asset_id,
            start_time=windowed_data.start_time,
            end_time=windowed_data.end_time,
            features=features_dict,
            feature_metadata={
                "window_id": windowed_data.window_id,
                "asset_type": asset_type,
                "num_features": len(features),
                "feature_types": list(set(f.feature_type for f in features)),
                "standardized": False
            }
        )
        
        return feature_vector
    
    def _accumulate_data(self, asset_id: str, features: List[ExtractedFeature]):
        """Accumule les features dans un buffer pour le traitement par lots"""
        # Pour le mode batch, on accumule les features par timestamp
        self.data_buffer[asset_id].extend(features)
        self.data_buffer[asset_id].sort(key=lambda x: x.timestamp)
    
    async def _process_and_publish_batch(
        self,
        asset_id: str,
        asset_type: Optional[str] = None
    ) -> Optional[ExtractedFeaturesVector]:
        """
        Traite les features accumulées par lots et crée un vecteur de features
        """
        try:
            current_buffer = self.data_buffer[asset_id]
            if not current_buffer:
                return None
            
            # Créer un vecteur de features à partir du buffer
            features_dict = {feature.feature_name: feature.feature_value for feature in current_buffer}
            
            # Créer le vecteur de features
            feature_vector_id = f"fv_{asset_id}_{uuid.uuid4().hex[:8]}"
            min_timestamp = min(f.timestamp for f in current_buffer)
            max_timestamp = max(f.timestamp for f in current_buffer)
            
            feature_vector = ExtractedFeaturesVector(
                feature_vector_id=feature_vector_id,
                timestamp=max_timestamp,
                asset_id=asset_id,
                start_time=min_timestamp,
                end_time=max_timestamp,
                features=features_dict,
                feature_metadata={
                    "asset_type": asset_type,
                    "num_features": len(current_buffer),
                    "feature_types": list(set(f.feature_type for f in current_buffer)),
                    "standardized": False
                }
            )
            
            # Standardiser le vecteur de features
            if settings.enable_standardization:
                feature_vector = self.standardization_service.standardize_feature_vector(
                    feature_vector,
                    asset_type
                )
            
            # Publier sur Kafka
            self.kafka_producer.publish_feature_vector(feature_vector)
            logger.info(f"Vecteur de features publié en batch: {feature_vector_id}")
            
            # Nettoyer le buffer
            self.data_buffer[asset_id].clear()
            
            return feature_vector
            
        except Exception as e:
            logger.error(f"Erreur lors de l'accumulation et traitement batch: {e}", exc_info=True)
            return None
    
    async def _get_asset_type(self, asset_id: str) -> Optional[str]:
        """
        Récupère le type d'actif depuis la base de données
        
        Args:
            asset_id: ID de l'actif
        
        Returns:
            Type d'actif ou None
        """
        try:
            # Récupérer le type d'actif depuis la base de données
            asset_type = self.asset_service.get_asset_type(asset_id)
            return asset_type
        except Exception as e:
            logger.error(f"Erreur lors de la récupération du type d'actif: {e}", exc_info=True)
            return None
    
    def get_buffer_size(self, asset_id: str) -> int:
        """Retourne la taille actuelle du buffer pour un asset"""
        return len(self.data_buffer[asset_id])
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Retourne les statistiques du service
        
        Returns:
            Dictionnaire avec les statistiques
        """
        return {
            "buffers": {
                asset_id: len(features) for asset_id, features in self.data_buffer.items()
            },
            "last_processed": {
                asset_id: last_time.isoformat() for asset_id, last_time in self.last_processed_time.items()
            },
            "services": {
                "temporal_features": settings.enable_temporal_features,
                "frequency_features": settings.enable_frequency_features,
                "wavelet_features": settings.enable_wavelet_features,
                "standardization": settings.enable_standardization,
                "feast": self.feast_service.is_available()
            }
        }

