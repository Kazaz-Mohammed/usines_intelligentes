package com.predictivemaintenance.ingestion.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.predictivemaintenance.ingestion.model.SensorData;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("TimescaleDBService Tests")
class TimescaleDBServiceTest {

    @Mock
    private JdbcTemplate jdbcTemplate;

    @Mock
    private ObjectMapper objectMapper;

    @InjectMocks
    private TimescaleDBService timescaleDBService;

    private SensorData testSensorData;

    @BeforeEach
    void setUp() throws Exception {
        testSensorData = SensorData.builder()
                .timestamp(Instant.now())
                .assetId("ASSET001")
                .sensorId("SENSOR001")
                .value(25.5)
                .unit("°C")
                .quality(2)
                .metadata(new HashMap<>())
                .sourceType("OPC_UA")
                .build();

        when(objectMapper.writeValueAsString(any(Map.class))).thenReturn("{}");
        when(jdbcTemplate.update(anyString(), any())).thenReturn(1);
    }

    @Test
    @DisplayName("Should insert sensor data into TimescaleDB")
    void testInsertSensorData() {
        // When
        timescaleDBService.insertSensorData(testSensorData);

        // Then
        ArgumentCaptor<Object[]> argsCaptor = ArgumentCaptor.forClass(Object[].class);
        verify(jdbcTemplate, times(1)).update(anyString(), argsCaptor.capture());

        Object[] args = argsCaptor.getValue();
        assertEquals(7, args.length);
        assertEquals("ASSET001", args[2]);
        assertEquals("SENSOR001", args[3]);
        assertEquals(25.5, args[4]);
    }

    @Test
    @DisplayName("Should insert batch of sensor data")
    void testInsertBatch() {
        // Given
        List<SensorData> dataList = new ArrayList<>();
        dataList.add(testSensorData);
        dataList.add(SensorData.builder()
                .timestamp(Instant.now())
                .assetId("ASSET002")
                .sensorId("SENSOR002")
                .value(30.0)
                .unit("°C")
                .quality(2)
                .metadata(new HashMap<>())
                .sourceType("OPC_UA")
                .build());

        // When
        timescaleDBService.insertBatch(dataList);

        // Then
        verify(jdbcTemplate, times(2)).update(anyString(), any());
    }

    @Test
    @DisplayName("Should handle empty metadata")
    void testInsertWithEmptyMetadata() throws Exception {
        // Given
        SensorData dataWithNullMetadata = SensorData.builder()
                .timestamp(Instant.now())
                .assetId("ASSET001")
                .sensorId("SENSOR001")
                .value(25.5)
                .unit("°C")
                .quality(2)
                .metadata(null)
                .sourceType("OPC_UA")
                .build();

        // When
        timescaleDBService.insertSensorData(dataWithNullMetadata);

        // Then
        verify(jdbcTemplate, times(1)).update(anyString(), any());
    }

    @Test
    @DisplayName("Should handle database error")
    void testInsertWithDatabaseError() {
        // Given
        when(jdbcTemplate.update(anyString(), any())).thenThrow(new RuntimeException("DB error"));

        // When/Then
        assertThrows(RuntimeException.class, () -> {
            timescaleDBService.insertSensorData(testSensorData);
        });
    }
}

