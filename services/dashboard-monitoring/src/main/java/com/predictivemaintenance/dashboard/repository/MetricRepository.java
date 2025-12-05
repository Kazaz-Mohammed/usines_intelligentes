package com.predictivemaintenance.dashboard.repository;

import com.predictivemaintenance.dashboard.model.Metric;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

/**
 * Repository pour les métriques
 */
@Repository
public interface MetricRepository extends JpaRepository<Metric, Long> {
    
    List<Metric> findByMetricNameAndTimestampBetweenOrderByTimestampAsc(
            String metricName, LocalDateTime start, LocalDateTime end
    );
    
    List<Metric> findByServiceNameAndTimestampBetweenOrderByTimestampAsc(
            String serviceName, LocalDateTime start, LocalDateTime end
    );
    
    Optional<Metric> findTopByMetricNameOrderByTimestampDesc(String metricName);
    
    @Query("SELECT m FROM Metric m ORDER BY m.timestamp DESC LIMIT :limit")
    List<Metric> findTopNByOrderByTimestampDesc(@Param("limit") int limit);
}

