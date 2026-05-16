import datetime
import sys
import re
import json
import os
from sqlalchemy.orm import Session
from models import SessionLocal, init_db, ThirteenF, Holding
from thirteen_f import import_filings, process_unprocessed_filings, client as sec_client
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

def log(msg):
    if tqdm:
        tqdm.write(msg)
    else:
        print(msg)

CHECKPOINT_FILE = "python_backend/checkpoint.json"

def save_checkpoint(year, quarter):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({"year": year, "quarter": quarter}, f)

def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r") as f:
                return json.load(f)
        except:
            return None
    return None

def import_all(period=None, verbose=False):
    """
    Imports and processes SEC 13F filings for a specified period or all history.
    
    Args:
        period (str): Optional. Format "YYYY" (e.g. 2026) or "YYYYQN" (e.g. 2026Q1).
                     If None, imports everything from 2014 to present.
        verbose (bool): If True, prints detailed request logs to the console.
    """
    start_time = datetime.datetime.now().astimezone()
    sec_client.verbose = verbose
    
    # Initialize the database (creates tables if they don't exist)
    init_db()
    db: Session = SessionLocal()
    
    # Get initial record counts
    initial_filings_count = db.query(ThirteenF).count()
    initial_holdings_count = db.query(Holding).count()
    
    
    # Determine current date to avoid requesting future quarters
    now = datetime.datetime.now().astimezone()
    current_year = now.year
    current_quarter = (now.month - 1) // 3 + 1
    
    # Default search range (SEC 13F XML data started in 2014)
    start_year = 2014
    end_year = current_year
    start_q = 1
    end_q = 4

    # Parse the user-provided period string
    if period:
        # Check for specific quarter format: e.g. "2026Q1"
        match_q = re.match(r"^(\d{4})Q([1-4])$", str(period).upper())
        # Check for full year format: e.g. "2026"
        match_y = re.match(r"^(\d{4})$", str(period))
        
        if match_q:
            # Set bounds to a single specific quarter
            start_year = end_year = int(match_q.group(1))
            start_q = end_q = int(match_q.group(2))
        elif match_y:
            # Set bounds to a full year (all 4 quarters)
            start_year = end_year = int(match_y.group(1))
            start_q = 1
            end_q = 4
        else:
            log(f"Invalid period format: {period}. Use YYYY (e.g. 2026) or YYYYQN (e.g. 2026Q1)")
            return

    checkpoint = load_checkpoint() if period is None else None
    if checkpoint:
        log(f"Found checkpoint. Resuming from {checkpoint['year']} Q{checkpoint['quarter']}")

    try:
        # Loop through each year in the range
        for year in range(start_year, end_year + 1):
            # Loop through each quarter (1-4)
            for quarter in range(1, 5):
                # Logic to skip quarters outside the requested bounds:
                
                # 1. Skip quarters before the start quarter in the first year
                if year == start_year and quarter < start_q:
                    continue
                # 2. Skip quarters after the end quarter in the last year
                if year == end_year and quarter > end_q:
                    break
                # 3. Safety check: Don't try to fetch data for the future
                if year == current_year and quarter > current_quarter:
                    break

                # 4. Resume from checkpoint if applicable
                if checkpoint and (year < checkpoint['year'] or (year == checkpoint['year'] and quarter < checkpoint['quarter'])):
                    continue

                save_checkpoint(year, quarter)
                
                # Step 1: Download the SEC Master Index for this quarter.
                # This file contains the names and CIKs of every manager who filed a 13F.
                log(f"{datetime.datetime.now().astimezone()}: Importing 13Fs for {year} Q{quarter}...")
                import_filings(db, filing_year=year, filing_quarter=quarter)
                
                # Step 2: Process the holdings for every manager found in the index.
                # This downloads the actual XML data for each filing (Primary Doc and Info Table).
                log(f"{datetime.datetime.now().astimezone()}: Processing holdings for {year} Q{quarter} (this may take a while)...")
                process_unprocessed_filings(db, filing_year=year, filing_quarter=quarter)
                
                log(f"{datetime.datetime.now().astimezone()}: Finished {year} Q{quarter}")
                
        # Get final record counts
        final_filings_count = db.query(ThirteenF).count()
        final_holdings_count = db.query(Holding).count()
        
        log(f"{datetime.datetime.now().astimezone()}: Import completed successfully.")
        log(f"Total SEC requests made: {sec_client.request_count}")
        log(f"Total filings in database: {final_filings_count} (+{final_filings_count - initial_filings_count} inserted)")
        log(f"Total holdings in database: {final_holdings_count} (+{final_holdings_count - initial_holdings_count} inserted)")
        
        end_time = datetime.datetime.now().astimezone()
        duration = end_time - start_time
        log(f"Total time: {duration}")
        if os.path.exists(CHECKPOINT_FILE):
            os.remove(CHECKPOINT_FILE)
    except Exception as e:
        log(f"Error during import: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    # Support command line arguments: e.g. "python import_all_filings.py 2026Q1"
    # If no argument is passed, sys.argv[1] is out of range, so we default to None.
    target_period = sys.argv[1] if len(sys.argv) > 1 else None
    
    # Run the import with verbose logging enabled
    import_all(period=target_period, verbose=True)
