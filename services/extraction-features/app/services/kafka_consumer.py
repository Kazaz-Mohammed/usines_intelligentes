"""
Service de consommation Kafka pour Extraction Features
"""
import logging
import json
from typing import List, Optional, Callable
from confluent_kafka import Consumer, KafkaError, KafkaException
from confluent_kafka.serialization import SerializationContext, MessageField
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.json_schema import JSONDeserializer

from app.config import settings
from app.models.feature_data import PreprocessedDataReference, WindowedDataReference

logger = logging.getLogger(__name__)


class KafkaConsumerService:
    """Service pour consommer des données depuis Kafka"""
    
    def __init__(self):
        self.config = {
            'bootstrap.servers': settings.kafka_bootstrap_servers,
            'group.id': settings.kafka_consumer_group,
            'auto.offset.reset': settings.kafka_auto_offset_reset,
            'enable.auto.commit': settings.kafka_enable_auto_commit,
            'session.timeout.ms': 30000,
            'max.poll.interval.ms': 300000,
        }
        self.consumer: Optional[Consumer] = None
        self._create_consumer()
    
    def _create_consumer(self):
        """Crée le consumer Kafka"""
        try:
            self.consumer = Consumer(self.config)
            logger.info(f"Consumer Kafka créé: {settings.kafka_bootstrap_servers}")
        except Exception as e:
            logger.error(f"Erreur lors de la création du consumer Kafka: {e}", exc_info=True)
            raise
    
    def consume_preprocessed_data(
        self,
        callback: Callable[[List[PreprocessedDataReference]], None],
        timeout: float = 1.0,
        max_messages: int = 100
    ):
        """
        Consomme des données prétraitées depuis Kafka
        
        Args:
            callback: Fonction à appeler avec les données consommées
            timeout: Timeout en secondes pour la consommation
            max_messages: Nombre maximum de messages à consommer par batch
        """
        if not self.consumer:
            raise RuntimeError("Consumer Kafka non initialisé")
        
        try:
            # S'abonner au topic
            self.consumer.subscribe([settings.kafka_topic_input_preprocessed])
            
            messages = []
            while True:
                msg = self.consumer.poll(timeout=timeout)
                
                if msg is None:
                    if messages:
                        # Traiter les messages accumulés
                        preprocessed_data = self._deserialize_preprocessed_data(messages)
                        if preprocessed_data:
                            callback(preprocessed_data)
                        messages = []
                    continue
                
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        logger.debug(f"Fin de partition atteinte: {msg.topic()}[{msg.partition()}]")
                        continue
                    else:
                        logger.error(f"Erreur Kafka: {msg.error()}")
                        raise KafkaException(msg.error())
                
                # Ajouter le message
                messages.append(msg)
                
                # Traiter si on a atteint le maximum
                if len(messages) >= max_messages:
                    preprocessed_data = self._deserialize_preprocessed_data(messages)
                    if preprocessed_data:
                        callback(preprocessed_data)
                    messages = []
                    
        except KeyboardInterrupt:
            logger.info("Interruption reçue, arrêt de la consommation")
        except Exception as e:
            logger.error(f"Erreur lors de la consommation: {e}", exc_info=True)
            raise
        finally:
            if self.consumer:
                self.consumer.close()
    
    def consume_windowed_data(
        self,
        callback: Callable[[WindowedDataReference], None],
        timeout: float = 1.0
    ):
        """
        Consomme des fenêtres de données depuis Kafka
        
        Args:
            callback: Fonction à appeler avec les fenêtres consommées
            timeout: Timeout en secondes pour la consommation
        """
        if not self.consumer:
            raise RuntimeError("Consumer Kafka non initialisé")
        
        try:
            # S'abonner au topic
            self.consumer.subscribe([settings.kafka_topic_input_windowed])
            
            while True:
                msg = self.consumer.poll(timeout=timeout)
                
                if msg is None:
                    continue
                
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        logger.debug(f"Fin de partition atteinte: {msg.topic()}[{msg.partition()}]")
                        continue
                    else:
                        logger.error(f"Erreur Kafka: {msg.error()}")
                        raise KafkaException(msg.error())
                
                # Désérialiser le message
                windowed_data = self._deserialize_windowed_data(msg)
                if windowed_data:
                    callback(windowed_data)
                    
        except KeyboardInterrupt:
            logger.info("Interruption reçue, arrêt de la consommation")
        except Exception as e:
            logger.error(f"Erreur lors de la consommation: {e}", exc_info=True)
            raise
        finally:
            if self.consumer:
                self.consumer.close()
    
    def _deserialize_preprocessed_data(
        self,
        messages: List
    ) -> List[PreprocessedDataReference]:
        """Désérialise les messages en PreprocessedDataReference"""
        preprocessed_data = []
        
        for msg in messages:
            try:
                # Désérialiser JSON
                data = json.loads(msg.value().decode('utf-8'))
                
                # Créer PreprocessedDataReference
                preprocessed_data.append(PreprocessedDataReference(**data))
                
            except Exception as e:
                logger.error(f"Erreur lors de la désérialisation: {e}", exc_info=True)
                continue
        
        return preprocessed_data
    
    def _deserialize_windowed_data(self, msg) -> Optional[WindowedDataReference]:
        """Désérialise un message en WindowedDataReference"""
        try:
            # Désérialiser JSON
            data = json.loads(msg.value().decode('utf-8'))
            
            # Convertir sensor_data en PreprocessedDataReference
            if 'sensor_data' in data:
                sensor_data = {}
                for sensor_id, sensor_list in data['sensor_data'].items():
                    sensor_data[sensor_id] = [
                        PreprocessedDataReference(**item) for item in sensor_list
                    ]
                data['sensor_data'] = sensor_data
            
            # Créer WindowedDataReference
            return WindowedDataReference(**data)
            
        except Exception as e:
            logger.error(f"Erreur lors de la désérialisation: {e}", exc_info=True)
            return None
    
    def close(self):
        """Ferme le consumer Kafka"""
        if self.consumer:
            self.consumer.close()
            logger.info("Consumer Kafka fermé")

