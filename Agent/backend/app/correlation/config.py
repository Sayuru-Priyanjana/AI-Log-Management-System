import os
from dotenv import load_dotenv

load_dotenv()

class CorrelationSettings:
    """
    Configuration for the Correlation Engine.
    These parameters control the thresholds and windows for detecting relationships
    and operational signals deterministically.
    """
    
    # Time window to consider evidence as temporally related (in seconds)
    CORRELATION_TIME_WINDOW_SECONDS: int = int(os.getenv("CORRELATION_TIME_WINDOW_SECONDS", "30").strip())
    
    # Threshold for consecutive errors within a window to trigger an ERROR_BURST signal
    ERROR_BURST_THRESHOLD: int = int(os.getenv("ERROR_BURST_THRESHOLD", "5").strip())
    ERROR_BURST_WINDOW_SECONDS: int = int(os.getenv("ERROR_BURST_WINDOW_SECONDS", "60").strip())
    
    # Threshold for consecutive 5xx errors to trigger HTTP_5XX_BURST signal
    HTTP_5XX_BURST_THRESHOLD: int = int(os.getenv("HTTP_5XX_BURST_THRESHOLD", "5").strip())
    HTTP_5XX_BURST_WINDOW_SECONDS: int = int(os.getenv("HTTP_5XX_BURST_WINDOW_SECONDS", "60").strip())
    
    # Thresholds for resource spikes
    CPU_SPIKE_PERCENT: float = float(os.getenv("CPU_SPIKE_PERCENT", "80.0").strip())
    MEMORY_SPIKE_PERCENT: float = float(os.getenv("MEMORY_SPIKE_PERCENT", "80.0").strip())


correlation_settings = CorrelationSettings()
