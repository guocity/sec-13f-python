import datetime
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session
from models import init_db, SessionLocal, ThirteenF
from thirteen_f import import_filings, process_unprocessed_filings, client as sec_client

DEFAULT_CIKS = [
    "0000102909",
    "0001067983",
    "0001167483",
    "0001603466"
]

class MinimalDbSeeder:
    def __init__(self, ciks=None, periods=None, verbose=False):
        self.ciks = ciks if ciks is not None else DEFAULT_CIKS
        self.verbose = verbose

        if periods is None:
            self.periods = []
            for i in range(4):
                date = datetime.date.today() - relativedelta(months=3 * i)
                self.periods.append({
                    "year": date.year,
                    "quarter": (date.month - 1) // 3 + 1
                })
        else:
            self.periods = periods

    def seed_minimal_db(self):
        sec_client.verbose = self.verbose
        init_db()
        db: Session = SessionLocal()

        start_time = datetime.datetime.utcnow()
        print(f"{start_time}: beginning minimal db seed, might take a few minutes…")

        try:
            for p in self.periods:
                year = p["year"]
                quarter = p["quarter"]

                print(f"{datetime.datetime.utcnow()}: importing 13Fs filed in {year} Q{quarter}")
                import_filings(db, filing_year=year, filing_quarter=quarter)

            print(f"{datetime.datetime.utcnow()}: processing filings data for sample managers")
            process_unprocessed_filings(db, ciks=self.ciks)

            print(f"{datetime.datetime.utcnow()}: deleting unprocessed filings not matching CIKs")
            db.query(ThirteenF).filter(
                ThirteenF.xml_data_fetched_at == None,
                ThirteenF.created_at > start_time,
                ~ThirteenF.cik.in_(self.ciks)
            ).delete(synchronize_session=False)
            db.commit()

            print(f"{datetime.datetime.utcnow()}: done, minimal db now available")
            print(f"Total SEC requests made: {sec_client.request_count}")
        except Exception as e:
            db.rollback()
            print(f"Error during seeding: {e}")
            raise
        finally:
            db.close()

if __name__ == "__main__":
    # Example to just run it and test minimal execution.
    seeder = MinimalDbSeeder(periods=[{"year": 2020, "quarter": 4}], ciks=["0001067983"], verbose=True)
    seeder.seed_minimal_db()
