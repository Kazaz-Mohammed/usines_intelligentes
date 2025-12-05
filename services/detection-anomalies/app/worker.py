"""
Worker en arrière-plan pour consommer Kafka et détecter les anomalies
"""
import logging
import signal
import sys
import threading
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from app.config import settings
from app.services.kafka_consumer import KafkaConsumerService
from app.services.kafka_producer import KafkaProducerService
from app.services.anomaly_detection_service import AnomalyDetectionService
from app.database.postgresql import PostgreSQLService
from app.models.anomaly_data import AnomalyDetectionRequest

logger = logging.getLogger(__name__)


class AnomalyDetectionWorker:
    """Worker pour consommer les features depuis Kafka et détecter les anomalies"""
    
    def __init__(self):
        self.running = False
        self.kafka_consumer = KafkaConsumerService()
        self.kafka_producer = KafkaProducerService()
        self.anomaly_detection_service = AnomalyDetectionService()
        self.postgresql_service = PostgreSQLService()
        
        # Gestion des signaux
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handler pour les signaux d'arrêt"""
        logger.info(f"Signal {signum} reçu, arrêt du worker...")
        self.stop()
        sys.exit(0)
    
    def start(self):
        """Démarre le worker"""
        if self.running:
            logger.warning("Worker déjà en cours d'exécution")
            return
        
        logger.info("Démarrage du worker de détection d'anomalies...")
        self.running = True
        
        try:
            # Vérifier que les modèles sont entraînés
            if not self.anomaly_detection_service.is_ready():
                logger.warning("Aucun modèle n'est entraîné. Le worker démarrera mais ne pourra pas détecter d'anomalies.")
                logger.warning("Entraînez les modèles via l'API POST /api/v1/anomalies/train")
            
            # Démarrer la consommation dans un thread séparé
            def consume_thread():
                try:
                    self.kafka_consumer.consume_features_continuous(
                        callback=self._process_feature,
                        timeout=1.0
                    )
                except Exception as e:
                    logger.error(f"Erreur dans le thread de consommation: {e}", exc_info=True)
                    self.running = False
            
            thread = threading.Thread(target=consume_thread, daemon=True)
            thread.start()
            logger.info("Thread de consommation démarré")
            
            # Attendre que le thread se termine
            thread.join()
            
        except Exception as e:
            logger.error(f"Erreur lors du démarrage du worker: {e}", exc_info=True)
            self.running = False
            raise
    
    def _process_feature(self, feature_data: Dict[str, Any]):
        """
        Traite une feature reçue depuis Kafka
        
        Args:
            feature_data: Données de la feature (dict JSON)
        """
        try:
            # Extraire les informations de la feature
            asset_id = feature_data.get("asset_id")
            sensor_id = feature_data.get("sensor_id")
            features = feature_data.get("features", {})
            timestamp = feature_data.get("timestamp")
            
            if not asset_id:
                logger.warning("Feature sans asset_id, ignorée")
                return
            
            if not features:
                logger.warning(f"Feature sans features pour {asset_id}, ignorée")
                return
            
            # Convertir timestamp si nécessaire
            if isinstance(timestamp, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                except:
                    timestamp = datetime.now(timezone.utc)
            elif timestamp is None:
                timestamp = datetime.now(timezone.utc)
            
            # Vérifier que les modèles sont prêts
            if not self.anomaly_detection_service.is_ready():
                logger.debug(f"Modèles non entraînés, feature ignorée: {asset_id}")
                return
            
            # Créer la requête de détection
            request = AnomalyDetectionRequest(
                asset_id=asset_id,
                sensor_id=sensor_id,
                features=features,
                timestamp=timestamp,
                metadata=feature_data.get("metadata", {})
            )
            
            # Détecter l'anomalie
            result = self.anomaly_detection_service.detect_anomaly(request)
            
            # Publier le résultat sur Kafka
            self.kafka_producer.publish_anomaly(result)
            
            # Journaliser l'anomalie dans la base de données si détectée
            if result.is_anomaly:
                try:
                    self.postgresql_service.insert_anomaly(result)
                except Exception as e:
                    logger.warning(f"Impossible de journaliser l'anomalie dans la base de données: {e}")
                
                logger.warning(
                    f"Anomalie détectée: {asset_id} "
                    f"(score={result.final_score:.3f}, "
                    f"criticality={result.criticality.value})"
                )
            else:
                logger.debug(f"Pas d'anomalie: {asset_id} (score={result.final_score:.3f})")
                
        except Exception as e:
            logger.error(f"Erreur lors du traitement de la feature: {e}", exc_info=True)
            # Ne pas lever l'exception pour continuer le traitement
    
    def stop(self):
        """Arrête le worker"""
        logger.info("Arrêt du worker...")
        self.running = False
        
        try:
            self.kafka_consumer.close()
            self.kafka_producer.close()
            self.postgresql_service.close()
        except Exception as e:
            logger.error(f"Erreur lors de la fermeture: {e}", exc_info=True)
        
        logger.info("Worker arrêté")


def main():
    """Point d'entrée principal pour le worker"""
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    worker = AnomalyDetectionWorker()
    try:
        worker.start()
    except KeyboardInterrupt:
        logger.info("Interruption reçue")
    finally:
        worker.stop()


if __name__ == "__main__":
    main()

