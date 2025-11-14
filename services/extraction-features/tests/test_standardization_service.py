"""
Tests pour le service de standardisation
"""
import pytest
from datetime import datetime
import numpy as np

from app.services.standardization_service import StandardizationService
from app.models.feature_data import ExtractedFeature, ExtractedFeaturesVector


class TestStandardizationService:
    """Tests pour StandardizationService"""

    @pytest.fixture
    def standardization_service(self):
        return StandardizationService()

    @pytest.fixture
    def sample_features(self):
        """Features d'exemple pour les tests"""
        base_time = datetime.utcnow()
        features = []

        for i in range(10):
            features.append(ExtractedFeature(
                timestamp=base_time,
                asset_id="ASSET001",
                sensor_id="SENSOR001",
                feature_name=f"feature_{i}",
                feature_value=25.0 + i * 0.5,
                feature_type="temporal",
                metadata={}
            ))

        return features

    @pytest.fixture
    def sample_feature_vector(self):
        """Vecteur de features d'exemple"""
        base_time = datetime.utcnow()

        return ExtractedFeaturesVector(
            feature_vector_id="fv_001",
            timestamp=base_time,
            asset_id="ASSET001",
            start_time=base_time,
            end_time=base_time,
            features={
                "rms": 25.5,
                "kurtosis": 2.3,
                "skewness": 0.5,
                "crest_factor": 4.1
            },
            feature_metadata={}
        )

    def test_standardize_features_z_score(self, standardization_service, sample_features):
        """Test standardisation z-score"""
        standardized = standardization_service.standardize_features(
            sample_features,
            asset_type="pump",
            method="z-score"
        )

        assert len(standardized) > 0
        assert all(isinstance(f, ExtractedFeature) for f in standardized)
        # Les valeurs standardisées devraient être centrées autour de 0
        values = [f.feature_value for f in standardized]
        mean_value = np.mean(values)
        assert abs(mean_value) < 1.0  # Approximativement centré

    def test_standardize_features_min_max(self, standardization_service, sample_features):
        """Test standardisation min-max"""
        standardized = standardization_service.standardize_features(
            sample_features,
            asset_type="pump",
            method="min-max"
        )

        assert len(standardized) > 0
        assert all(isinstance(f, ExtractedFeature) for f in standardized)
        # Les valeurs min-max devraient être entre 0 et 1
        values = [f.feature_value for f in standardized]
        assert all(0 <= v <= 1 for v in values)

    def test_standardize_features_robust(self, standardization_service, sample_features):
        """Test standardisation robust"""
        standardized = standardization_service.standardize_features(
            sample_features,
            asset_type="pump",
            method="robust"
        )

        assert len(standardized) > 0
        assert all(isinstance(f, ExtractedFeature) for f in standardized)

    def test_standardize_feature_vector(self, standardization_service, sample_feature_vector):
        """Test standardisation d'un vecteur de features"""
        standardized = standardization_service.standardize_feature_vector(
            sample_feature_vector,
            asset_type="pump",
            method="z-score"
        )

        assert isinstance(standardized, ExtractedFeaturesVector)
        assert standardized.feature_vector_id == sample_feature_vector.feature_vector_id
        assert len(standardized.features) == len(sample_feature_vector.features)
        # Les valeurs devraient être standardisées
        assert standardized.feature_metadata.get("standardized", False) is True

    def test_standardize_features_empty(self, standardization_service):
        """Test avec features vides"""
        standardized = standardization_service.standardize_features(
            [],
            asset_type="pump"
        )
        assert len(standardized) == 0

    def test_update_asset_type_template(self, standardization_service):
        """Test mise à jour du template d'actif"""
        # Créer des features pour un type d'actif
        features = [
            ExtractedFeature(
                timestamp=datetime.utcnow(),
                asset_id="ASSET001",
                sensor_id="SENSOR001",
                feature_name="rms",
                feature_value=25.0 + i * 0.5,
                feature_type="temporal",
                metadata={}
            ) for i in range(10)
        ]

        # Mettre à jour le template
        standardization_service.update_asset_type_template(
            "pump",
            features
        )

        # Vérifier que le template a été mis à jour
        assert "pump" in standardization_service.asset_type_templates
        template = standardization_service.asset_type_templates["pump"]
        assert "rms" in template
        assert "mean" in template["rms"]
        assert "std" in template["rms"]

