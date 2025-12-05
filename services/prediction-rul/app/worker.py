"""
Worker Kafka pour traitement temps-réel des prédictions RUL
"""
import logging
import signal
import sys
from datetime import datetime, timezone
from typing import Dict, Any

from app.config import settings
from app.services.kafka_consumer import KafkaConsumerService
from app.services.kafka_producer import KafkaProducerService
from app.services.rul_prediction_service import RULPredictionService
from app.database.postgresql import PostgreSQLService
from app.models.rul_data import RULPredictionRequest, RULPredictionResult

logger = logging.getLogger(__name__)


class RULPredictionWorker:
    """Worker pour traitement temps-réel des prédictions RUL depuis Kafka"""
    
    def __init__(self):
        """Initialise le worker"""
        self.running = False
        self.kafka_consumer = KafkaConsumerService()
        self.kafka_producer = KafkaProducerService()
        self.rul_prediction_service = RULPredictionService()
        self.postgresql_service = PostgreSQLService()
        
        # S'abonner au topic des features
        self.kafka_consumer.subscribe([settings.kafka_topic_input_features])
        
        # Gestion des signaux
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        logger.info("RUL Prediction Worker initialisé")
    
    def _signal_handler(self, signum, frame):
        """Gestionnaire de signaux pour arrêt propre"""
        logger.info(f"Signal {signum} reçu, arrêt du worker...")
        self.stop()
    
    def start(self):
        """Démarre le worker en mode streaming"""
        if self.running:
            logger.warning("Worker déjà en cours d'exécution")
            return
        
        self.running = True
        logger.info("Démarrage du RUL Prediction Worker...")
        
        # Vérifier que les modèles sont entraînés
        if not self.rul_prediction_service.is_ready():
            logger.warning("Aucun modèle n'est entraîné. Le worker démarrera mais les prédictions échoueront.")
            logger.warning("Entraînez les modèles via POST /api/v1/rul/train avant de démarrer le worker.")
        
        try:
            self._start_streaming_mode()
        except Exception as e:
            logger.error(f"Erreur dans le worker: {e}", exc_info=True)
            raise
        finally:
            self.stop()
    
    def _start_streaming_mode(self):
        """Mode streaming : consomme et traite les messages en continu"""
        logger.info("Mode streaming activé, consommation des features...")
        
        def process_feature_message(message_data: Dict[str, Any]):
            """Traite un message de features"""
            try:
                # Extraire les informations du message
                asset_id = message_data.get('asset_id')
                sensor_id = message_data.get('sensor_id')
                features = message_data.get('features', {})
                timestamp = message_data.get('timestamp')
                metadata = message_data.get('metadata', {})
                
                if not asset_id:
                    logger.warning("Message sans asset_id, ignoré")
                    return
                
                if not features:
                    logger.warning(f"Message sans features pour {asset_id}, ignoré")
                    return
                
                # Convertir timestamp si string
                if isinstance(timestamp, str):
                    try:
                        timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    except:
                        timestamp = datetime.now(timezone.utc)
                elif timestamp is None:
                    timestamp = datetime.now(timezone.utc)
                
                # Créer la requête de prédiction
                prediction_request = RULPredictionRequest(
                    asset_id=asset_id,
                    sensor_id=sensor_id,
                    features=features,
                    timestamp=timestamp,
                    metadata=metadata
                )
                
                # Prédire la RUL
                try:
                    rul_result = self.rul_prediction_service.predict_rul(
                        prediction_request,
                        use_ensemble=True
                    )
                    
                    # Publier la prédiction sur Kafka
                    self.kafka_producer.publish_rul_prediction(rul_result)
                    
                    # Journaliser dans PostgreSQL
                    try:
                        self.postgresql_service.insert_rul_prediction(rul_result)
                    except Exception as e:
                        logger.warning(f"Impossible de journaliser la prédiction RUL dans PostgreSQL: {e}")
                    
                    logger.info(
                        f"RUL prédite pour {asset_id}: {rul_result.rul_prediction:.2f} "
                        f"(intervalle: [{rul_result.confidence_interval_lower:.2f}, "
                        f"{rul_result.confidence_interval_upper:.2f}])"
                    )
                    
                except RuntimeError as e:
                    logger.warning(f"Modèle non entraîné, prédiction ignorée pour {asset_id}: {e}")
                except Exception as e:
                    logger.error(f"Erreur lors de la prédiction RUL pour {asset_id}: {e}", exc_info=True)
            
            except Exception as e:
                logger.error(f"Erreur lors du traitement du message: {e}", exc_info=True)
        
        # Consommer en continu
        self.kafka_consumer.consume_features_continuous(
            callback=process_feature_message,
            timeout=1.0
        )
    
    def stop(self):
        """Arrête le worker"""
        if not self.running:
            return
        
        logger.info("Arrêt du RUL Prediction Worker...")
        self.running = False
        
        try:
            self.kafka_consumer.close()
            self.kafka_producer.close()
            logger.info("Worker arrêté proprement")
        except Exception as e:
            logger.error(f"Erreur lors de l'arrêt du worker: {e}", exc_info=True)


def main():
    """Point d'entrée principal du worker"""
    # Configuration du logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    worker = RULPredictionWorker()
    
    try:
        worker.start()
    except KeyboardInterrupt:
        logger.info("Interruption clavier")
    except Exception as e:
        logger.error(f"Erreur fatale: {e}", exc_info=True)
        sys.exit(1)
    finally:
        worker.stop()


if __name__ == "__main__":
    main()

