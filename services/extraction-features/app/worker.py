"""
Worker en arrière-plan pour consommer Kafka et extraire les features
"""
import logging
import signal
import sys
from typing import Optional
from datetime import datetime

from app.config import settings
from app.services.kafka_consumer import KafkaConsumerService
from app.services.feature_extraction_service import FeatureExtractionService
from app.models.feature_data import PreprocessedDataReference, WindowedDataReference

logger = logging.getLogger(__name__)


class FeatureExtractionWorker:
    """Worker en arrière-plan pour l'extraction de features"""
    
    def __init__(self):
        self.running = False
        self.kafka_consumer = KafkaConsumerService()
        self.feature_extraction_service = FeatureExtractionService()
        self.mode = "streaming"  # "streaming" ou "batch"
        
        # Gestion des signaux
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handler pour les signaux d'arrêt"""
        logger.info(f"Signal {signum} reçu, arrêt du worker...")
        self.stop()
        sys.exit(0)
    
    def start(self, mode: str = "streaming"):
        """
        Démarre le worker
        
        Args:
            mode: Mode de traitement ("streaming" ou "batch")
        """
        if self.running:
            logger.warning("Worker déjà en cours d'exécution")
            return
        
        self.mode = mode
        self.running = True
        
        logger.info(f"Démarrage du worker d'extraction de features en mode: {mode}")
        
        try:
            # Démarrer la consommation depuis les topics
            if mode == "streaming":
                self._start_streaming_mode()
            elif mode == "batch":
                self._start_batch_mode()
            else:
                logger.error(f"Mode inconnu: {mode}")
                return
        except Exception as e:
            logger.error(f"Erreur lors du démarrage du worker: {e}", exc_info=True)
            self.running = False
            raise
    
    def _start_streaming_mode(self):
        """Démarre le mode streaming"""
        logger.info("Démarrage du mode streaming...")
        
        # Consommer depuis le topic preprocessed-data
        def preprocessed_data_handler(preprocessed_data: List[PreprocessedDataReference]):
            """Handler pour les données prétraitées"""
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    self.feature_extraction_service.process_preprocessed_data(
                        preprocessed_data,
                        mode="streaming"
                    )
                )
                loop.close()
            except Exception as e:
                logger.error(f"Erreur lors du traitement des données prétraitées: {e}", exc_info=True)
        
        # Démarrer la consommation
        self.kafka_consumer.consume_preprocessed_data(
            preprocessed_data_handler,
            timeout=1.0,
            max_messages=100
        )
    
    def _start_batch_mode(self):
        """Démarre le mode batch"""
        logger.info("Démarrage du mode batch...")
        
        # Consommer depuis le topic windowed-data
        def windowed_data_handler(windowed_data: WindowedDataReference):
            """Handler pour les fenêtres de données"""
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    self.feature_extraction_service.process_windowed_data(windowed_data)
                )
                loop.close()
            except Exception as e:
                logger.error(f"Erreur lors du traitement des fenêtres de données: {e}", exc_info=True)
        
        # Démarrer la consommation
        self.kafka_consumer.consume_windowed_data(
            windowed_data_handler,
            timeout=1.0
        )
    
    def stop(self):
        """Arrête le worker"""
        if not self.running:
            return
        
        self.running = False
        
        # Arrêter la consommation Kafka
        if self.kafka_consumer:
            self.kafka_consumer.close()
        
        # Fermer les services
        if self.feature_extraction_service:
            # Fermer les services si nécessaire
            pass
        
        logger.info("Worker d'extraction de features arrêté")

