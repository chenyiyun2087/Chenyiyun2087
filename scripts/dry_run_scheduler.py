import datetime
import logging
import sys
import os
from unittest.mock import MagicMock, patch

# Add parent directory to path to import scheduler
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock modules before importing scheduler
sys.modules['sqlalchemy'] = MagicMock()
sys.modules['pandas'] = MagicMock()

# Mock specific functions in scheduler
with patch('scheduler.is_trade_day', return_value=True), \
     patch('scheduler.is_data_ready', return_value=True), \
     patch('scheduler.run_script', return_value=True) as mock_run_script, \
     patch('time.sleep', side_effect=KeyboardInterrupt) as mock_sleep: # Raise interrupt to stop loop

    # Import scheduler after patching
    import scheduler
    
    # Mock datetime to 21:00
    class MockDateTime(datetime.datetime):
        @classmethod
        def now(cls):
            # Return a time that triggers the pipeline
            return datetime.datetime(2026, 2, 9, 21, 0, 0)
            
    scheduler.datetime.datetime = MockDateTime
    
    # Run main
    print("Starting simulation...")
    try:
        scheduler.main()
    except KeyboardInterrupt:
        print("Simulation stopped.")
        
    # Verify pipeline steps
    print("\nVerifying calls:")
    expected_calls = [
        "Eastmoney/run_strategy.py",
        "ScoreRank/run_daily.py",
        "Sina/live_tracker/run_live_tracker.py"
    ]
    
    calls = [args[0][0] for args in mock_run_script.call_args_list]
    for script in expected_calls:
        if script in calls:
            print(f"[PASS] {script} was called.")
        else:
            print(f"[FAIL] {script} was NOT called.")
