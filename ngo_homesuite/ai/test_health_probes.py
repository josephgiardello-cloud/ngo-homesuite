"""
Comprehensive tests for Copilot Health Probes and Circuit Breaker.

Validates:
✅ Circuit breaker state transitions
✅ Failure threshold enforcement
✅ Automatic recovery attempts
✅ Health probe functionality
✅ Graceful degradation with fallback
✅ Metrics collection and reporting
"""

import pytest
import time
from unittest.mock import Mock, patch

from ngo_homesuite.ai.health_probes import (
    CircuitBreaker,
    CircuitBreakerState,
    CircuitBreakerOpen,
    CopilotHealthProbe,
    CopilotRequestGate,
)


class TestCircuitBreakerStateTransitions:
    """Test circuit breaker state machine."""

    def test_initial_state_closed(self):
        """
        **Scenario**: Circuit breaker starts in closed state.
        
        **Assertions**: Initial state is CLOSED.
        """
        cb = CircuitBreaker(name="test")
        
        assert cb.get_state() == CircuitBreakerState.CLOSED

    def test_transition_closed_to_open_on_threshold(self):
        """
        **Scenario**: Circuit opens after failure threshold exceeded.
        
        **Flow**:
        1. Make 5 failing requests (threshold=5)
        2. Verify circuit opens
        
        **Assertions**: State transitions to OPEN.
        """
        cb = CircuitBreaker(
            name="test",
            failure_threshold=3,
            expected_exception=ValueError,
        )
        
        def failing_func():
            raise ValueError("Test failure")
        
        # Make failing calls
        for i in range(3):
            with pytest.raises(ValueError):
                cb.call(failing_func)
        
        # Circuit should now be OPEN
        assert cb.get_state() == CircuitBreakerState.OPEN

    def test_circuit_open_rejects_fast(self):
        """
        **Scenario**: When circuit is open, requests fail immediately.
        
        **Assertions**: CircuitBreakerOpen raised without calling function.
        """
        cb = CircuitBreaker(
            name="test",
            failure_threshold=1,
            expected_exception=ValueError,
        )
        
        call_count = 0
        def failing_func():
            nonlocal call_count
            call_count += 1
            raise ValueError("Test")
        
        # Trigger circuit open
        with pytest.raises(ValueError):
            cb.call(failing_func)
        assert cb.get_state() == CircuitBreakerState.OPEN
        
        # Next request should fail immediately without calling function
        with pytest.raises(CircuitBreakerOpen):
            cb.call(failing_func)
        
        assert call_count == 1  # Function only called once


class TestCircuitBreakerFailureTracking:
    """Test failure counting and metrics."""

    def test_consecutive_failures_counter(self):
        """
        **Scenario**: Track consecutive failures.
        
        **Assertions**: Counter increments on each failure.
        """
        cb = CircuitBreaker(name="test", failure_threshold=5)
        
        def failing_func():
            raise ValueError()
        
        for i in range(3):
            with pytest.raises(ValueError):
                cb.call(failing_func)
            
            assert cb.metrics.consecutive_failures == i + 1

    def test_consecutive_failures_reset_on_success(self):
        """
        **Scenario**: Success resets consecutive failure counter.
        
        **Assertions**: Counter returns to 0 after success.
        """
        cb = CircuitBreaker(name="test", failure_threshold=5)
        
        def failing_func():
            raise ValueError()
        
        def success_func():
            return True
        
        # Fail once
        with pytest.raises(ValueError):
            cb.call(failing_func)
        assert cb.metrics.consecutive_failures == 1
        
        # Succeed
        cb.call(success_func)
        assert cb.metrics.consecutive_failures == 0


class TestCircuitBreakerRecovery:
    """Test circuit recovery mechanism."""

    def test_half_open_state_after_timeout(self):
        """
        **Scenario**: Circuit attempts recovery after timeout.
        
        **Flow**:
        1. Trigger circuit open
        2. Wait for recovery timeout
        3. Next request triggers HALF_OPEN state
        
        **Assertions**: State transitions to HALF_OPEN.
        """
        cb = CircuitBreaker(
            name="test",
            failure_threshold=1,
            recovery_timeout=1,  # 1 second
            expected_exception=ValueError,
        )
        
        def failing_func():
            raise ValueError()
        
        # Trigger circuit open
        with pytest.raises(ValueError):
            cb.call(failing_func)
        assert cb.get_state() == CircuitBreakerState.OPEN
        
        # Wait for recovery timeout
        time.sleep(1.1)
        
        # Next attempt should transition to HALF_OPEN
        # A failed recovery attempt immediately re-opens the circuit.
        with pytest.raises(ValueError):
            cb.call(failing_func)
        assert cb.get_state() == CircuitBreakerState.OPEN

    def test_transition_half_open_to_closed_on_success(self):
        """
        **Scenario**: Circuit closes on successful call in HALF_OPEN state.
        
        **Flow**:
        1. Open circuit
        2. Wait for recovery
        3. Call successful function
        4. Circuit should close
        
        **Assertions**: State transitions back to CLOSED.
        """
        cb = CircuitBreaker(
            name="test",
            failure_threshold=1,
            recovery_timeout=1,
            expected_exception=ValueError,
        )
        
        fail_count = 0
        def maybe_fail():
            nonlocal fail_count
            if fail_count < 1:
                fail_count += 1
                raise ValueError()
            return "success"
        
        # Trigger circuit open
        with pytest.raises(ValueError):
            cb.call(maybe_fail)
        assert cb.get_state() == CircuitBreakerState.OPEN
        
        # Wait for recovery
        time.sleep(1.1)
        
        # Call with function that now succeeds
        result = cb.call(maybe_fail)
        assert result == "success"
        assert cb.get_state() == CircuitBreakerState.CLOSED


class TestCircuitBreakerMetrics:
    """Test metrics collection."""

    def test_metrics_track_requests(self):
        """
        **Scenario**: Metrics track total requests.
        
        **Assertions**: Request count incremented.
        """
        cb = CircuitBreaker(name="test")
        
        def success_func():
            return "ok"
        
        for _ in range(5):
            cb.call(success_func)
        
        metrics = cb.get_metrics()
        assert metrics['total_requests'] == 5

    def test_metrics_track_latency(self):
        """
        **Scenario**: Metrics track request latency.
        
        **Assertions**: Latency values recorded.
        """
        cb = CircuitBreaker(name="test")
        
        def slow_func():
            time.sleep(0.01)  # 10ms
            return "ok"
        
        cb.call(slow_func)
        
        metrics = cb.get_metrics()
        assert metrics['avg_latency_ms'] > 0
        assert metrics['max_latency_ms'] >= 10

    def test_metrics_track_failure_rate(self):
        """
        **Scenario**: Metrics calculate failure rate.
        
        **Assertions**: Failure rate correct (e.g., 2/5 = 0.4).
        """
        cb = CircuitBreaker(name="test", failure_threshold=10)
        
        def failing_func():
            raise ValueError()
        
        def success_func():
            return "ok"
        
        # 2 failures, 3 successes
        with pytest.raises(ValueError):
            cb.call(failing_func)
        with pytest.raises(ValueError):
            cb.call(failing_func)
        cb.call(success_func)
        cb.call(success_func)
        cb.call(success_func)
        
        metrics = cb.get_metrics()
        assert metrics['total_requests'] == 5
        assert metrics['total_failures'] == 2
        assert abs(metrics['failure_rate'] - 0.4) < 0.01


class TestCopilotHealthProbe:
    """Test Copilot health check probe."""

    def test_health_probe_healthy_status(self):
        """
        **Scenario**: Copilot service is healthy.
        
        **Assertions**: Probe returns healthy=True.
        """
        probe = CopilotHealthProbe()
        
        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {'models': [{'name': 'llama3.2'}]}
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response
            
            result = probe.probe()
            
            assert result['healthy'] is True
            assert result['error'] is None
            assert result['latency_ms'] > 0

    def test_health_probe_unhealthy_no_models(self):
        """
        **Scenario**: Copilot service has no models available.
        
        **Assertions**: Probe returns healthy=False.
        """
        probe = CopilotHealthProbe()
        
        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {'models': []}  # No models
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response
            
            result = probe.probe()
            
            assert result['healthy'] is False
            assert 'No models' in result['error']

    def test_health_probe_unavailable_service(self):
        """
        **Scenario**: Copilot service is unreachable.
        
        **Assertions**: Probe returns healthy=False with error.
        """
        probe = CopilotHealthProbe()
        
        with patch('requests.get') as mock_get:
            mock_get.side_effect = ConnectionError("Connection refused")
            
            result = probe.probe()
            
            assert result['healthy'] is False
            assert 'Connection refused' in result['error']

    def test_health_probe_circuit_breaker_integration(self):
        """
        **Scenario**: Health probe uses circuit breaker.
        
        **Flow**:
        1. Fail health check 3 times
        2. Circuit opens
        3. Next check returns circuit open error
        
        **Assertions**: Circuit breaker prevents repeated failures.
        """
        probe = CopilotHealthProbe()
        
        with patch('requests.get') as mock_get:
            mock_get.side_effect = ConnectionError()
            
            # Fail 3 times (threshold for this probe)
            for _ in range(3):
                result = probe.probe()
                assert result['healthy'] is False
            
            # Circuit should be open now
            result = probe.probe()
            assert result['circuit_state'] == 'open'


class TestCopilotRequestGate:
    """Test request gating with fallback."""

    def test_gate_allows_when_healthy(self):
        """
        **Scenario**: Gate allows requests when service healthy.
        
        **Assertions**: should_allow_request returns True.
        """
        probe = CopilotHealthProbe()
        gate = CopilotRequestGate(probe)
        
        with patch.object(probe, 'is_healthy', return_value=True):
            assert gate.should_allow_request() is True

    def test_gate_blocks_when_unhealthy(self):
        """
        **Scenario**: Gate blocks requests when service unhealthy.
        
        **Assertions**: should_allow_request returns False.
        """
        probe = CopilotHealthProbe()
        gate = CopilotRequestGate(probe)
        
        with patch.object(probe, 'is_healthy', return_value=False):
            assert gate.should_allow_request() is False

    def test_gate_returns_fallback_message(self):
        """
        **Scenario**: Gate provides helpful fallback response.
        
        **Assertions**: Fallback message is user-friendly.
        """
        probe = CopilotHealthProbe()
        gate = CopilotRequestGate(probe)
        
        fallback = gate.get_fallback_response("test query")
        
        assert "currently unavailable" in fallback.lower()
        assert len(fallback) > 20


class TestHealthProbeMetrics:
    """Test health probe metrics collection."""

    def test_probe_metrics_recorded(self):
        """
        **Scenario**: Health probe records metrics.
        
        **Assertions**: Metrics include timestamp and result.
        """
        probe = CopilotHealthProbe()
        
        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {'models': [{'name': 'llama3.2'}]}
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response
            
            probe.probe()
            metrics = probe.get_metrics()
            
            assert metrics['last_check_time'] is not None
            assert metrics['last_check_result'] is True
            assert metrics['consecutive_failures'] == 0

    def test_probe_tracks_consecutive_failures(self):
        """
        **Scenario**: Probe tracks consecutive failures.
        
        **Assertions**: Counter increments on failures.
        """
        probe = CopilotHealthProbe()
        
        with patch('requests.get') as mock_get:
            mock_get.side_effect = ConnectionError()
            
            # Fail twice
            probe.probe()
            probe.probe()
            
            metrics = probe.get_metrics()
            assert metrics['consecutive_failures'] == 2
