import datetime
from sqlalchemy.orm import Session
from models import ThirteenF, Holding, AggregateHolding
from sec_client import SecClient, XmlUrlsNotFound
from sqlalchemy import func
from tqdm import tqdm
import requests

client = SecClient()

FIRST_YEAR_EXPECTED_TO_HAVE_XML_URLS = 2014

def import_filings(db: Session, filing_year: int, filing_quarter: int):
    """
    Downloads the SEC master index for a quarter and saves the metadata 
    of all found 13F filings into the 'thirteen_f' table.
    """
    now = datetime.datetime.now().astimezone()
    rows = client.thirteen_f_filings(filing_year=filing_year, filing_quarter=filing_quarter)

    if not rows:
        return

    for row in tqdm(rows, desc=f"Importing {filing_year} Q{filing_quarter} Index", unit="filing"):
        existing = db.query(ThirteenF).filter(ThirteenF.external_id == row["external_id"]).first()
        if not existing:
            filing = ThirteenF(
                external_id=row["external_id"],
                cik=row["cik"],
                name=row["company_name"],
                form_type=row["form_type"],
                date_filed=row["date_filed"],
                directory_url=row["directory_url"],
                filing_year=filing_year,
                filing_quarter=filing_quarter,
                created_at=now,
                updated_at=now
            )
            db.add(filing)
    db.commit()

def process_unprocessed_filings(db: Session, filing_year=None, filing_quarter=None, name_starts=None, ciks=None):
    """
    Finds filings in the DB that haven't been processed yet and fetches their holdings data.
    Can be filtered by year, quarter, name prefix, or CIK.
    """
    query = db.query(ThirteenF).filter(ThirteenF.xml_data_fetched_at == None)

    if filing_year is not None:
        query = query.filter(ThirteenF.filing_year == filing_year)
    if filing_quarter is not None:
        query = query.filter(ThirteenF.filing_quarter == filing_quarter)
    if name_starts is not None:
        query = query.filter(ThirteenF.name.ilike(f"{name_starts}%"))
    if ciks is not None:
        if isinstance(ciks, list):
            query = query.filter(ThirteenF.cik.in_(ciks))
        else:
            query = query.filter(ThirteenF.cik == ciks)

    # For better progress reporting, count total and already processed filings
    total_q = db.query(ThirteenF)
    if filing_year: total_q = total_q.filter(ThirteenF.filing_year == filing_year)
    if filing_quarter: total_q = total_q.filter(ThirteenF.filing_quarter == filing_quarter)
    total_count = total_q.count()
    
    processed_count = total_q.filter(ThirteenF.xml_data_fetched_at != None).count()

    unprocessed = query.all()
    
    desc = f"Processing {filing_year} Q{filing_quarter}" if filing_year and filing_quarter else "Processing Holdings"
    for filing in tqdm(unprocessed, desc=desc, unit="filing", total=total_count, initial=processed_count):
        process_filing(db, filing)

def process_filing(db: Session, filing: ThirteenF, force=False):
    """
    The main processing pipeline for a single filing:
    1. Fetch XML URLs and content
    2. Parse Primary Doc (manager info)
    3. Parse Info Table (stock holdings)
    """
    if filing.xml_data_fetched_at is not None and not force:
        return

    try:
        primary_xml, info_xml = fetch_xml_content(db, filing)
        
        if primary_xml:
            parse_primary_doc(db, filing, primary_xml)
        
        if info_xml:
            parse_info_table(db, filing, info_xml)
        
        filing.xml_data_fetched_at = datetime.datetime.now().astimezone()
        db.commit()
    except Exception as e:
        error_type = type(e).__name__
        msg = f"\nSkipping filing {filing.external_id} ({filing.name}) due to {error_type}: {e}"
        tqdm.write(msg)
        db.rollback()

def fetch_xml_content(db: Session, filing: ThirteenF):
    try:
        xml_urls = client.xml_urls(filing.directory_url)

        primary_doc_url = client.primary_doc_url(xml_urls)
        filing.primary_doc_url = primary_doc_url
        primary_xml = None
        if primary_doc_url:
            primary_xml = client.get(primary_doc_url).content.decode('utf-8', errors='replace')

        info_table_url = client.info_table_url(xml_urls)
        filing.info_table_url = info_table_url
        info_xml = None
        if info_table_url:
            info_xml = client.get(info_table_url).content.decode('utf-8', errors='replace')

        return primary_xml, info_xml
    except XmlUrlsNotFound:
        if expected_to_have_xml_urls(filing):
            raise
        return None, None

def expected_to_have_xml_urls(filing: ThirteenF):
    return filing.filing_year >= FIRST_YEAR_EXPECTED_TO_HAVE_XML_URLS

def parse_primary_doc(db: Session, filing: ThirteenF, xml_content: str):
    if not xml_content:
        return

    parsed = client.parse_primary_doc_xml(xml_content)

    filing.report_date = parsed.get("report_date")
    if filing.report_date:
        filing.report_year = filing.report_date.year
        filing.report_quarter = (filing.report_date.month - 1) // 3 + 1

    filing.street1 = parsed.get("street1")
    filing.street2 = parsed.get("street2")
    filing.city = parsed.get("city")
    filing.state_or_country = parsed.get("state_or_country")
    filing.zip_code = parsed.get("zip_code")

    oimc = parsed.get("other_included_managers_count")
    filing.other_included_managers_count = int(oimc) if oimc and oimc.isdigit() else None

    hcr = parsed.get("holdings_count_reported")
    filing.holdings_count_reported = int(hcr) if hcr and hcr.isdigit() else None

    hvr = parsed.get("holdings_value_reported")
    try:
        filing.holdings_value_reported = float(hvr) if hvr else None
    except ValueError:
        filing.holdings_value_reported = None

    filing.confidential_omitted = str(parsed.get("confidential_omitted")).lower() == 'true' if parsed.get("confidential_omitted") else False
    filing.other_managers = parsed.get("other_managers", [])
    filing.report_type = parsed.get("report_type")
    filing.amendment_type = parsed.get("amendment_type")
    filing.amendment_number = parsed.get("amendment_number")
    filing.file_number = parsed.get("file_number")

    db.commit()
    mark_previous_filings_as_restated(db, filing)

def is_restatement(filing: ThirteenF):
    return filing.amendment_type == "restatement"

def mark_previous_filings_as_restated(db: Session, filing: ThirteenF):
    if not is_restatement(filing):
        return

    query = db.query(ThirteenF).filter(
        ThirteenF.cik == filing.cik,
        ThirteenF.report_date == filing.report_date,
        ThirteenF.id != filing.id,
        ThirteenF.date_filed <= filing.date_filed
    )

    for prev_filing in query.all():
        # Handle amendment logic
        if prev_filing.amendment_type is None or (
            prev_filing.amendment_type == "restatement" and
            prev_filing.amendment_number is not None and
            filing.amendment_number is not None and
            prev_filing.amendment_number < filing.amendment_number
        ):
            prev_filing.restated_by_id = filing.id

    db.commit()

def has_no_info_table(filing: ThirteenF):
    return filing.xml_data_fetched_at is not None and not filing.info_table_url

def parse_info_table(db: Session, filing: ThirteenF, xml_content: str):
    if not xml_content:
        return

    parsed_holdings = client.parse_info_table_xml(xml_content)

    # Delete existing holdings
    db.query(Holding).filter(Holding.thirteen_f_id == filing.id).delete()
    db.query(AggregateHolding).filter(AggregateHolding.thirteen_f_id == filing.id).delete()

    now = datetime.datetime.now().astimezone()
    holdings_to_insert = []

    for row in parsed_holdings:
        def to_numeric(val):
            if val is None: return None
            try: return float(val)
            except ValueError: return None

        def to_bigint(val):
            if val is None: return None
            try: return int(val)
            except ValueError: return None

        holdings_to_insert.append(Holding(
            thirteen_f_id=filing.id,
            cusip=row.get("cusip"),
            issuer_name=row.get("issuer_name"),
            class_title=row.get("class_title"),
            value=row.get("value"),
            shares_or_principal_amount=to_numeric(row.get("shares_or_principal_amount")),
            shares_or_principal_amount_type=row.get("shares_or_principal_amount_type"),
            option_type=row.get("option_type"),
            investment_discretion=row.get("investment_discretion"),
            other_manager=row.get("other_manager"),
            voting_authority_sole=to_bigint(row.get("voting_authority_sole")),
            voting_authority_shared=to_bigint(row.get("voting_authority_shared")),
            voting_authority_none=to_bigint(row.get("voting_authority_none")),
            created_at=now,
            updated_at=now
        ))

    if holdings_to_insert:
        db.bulk_save_objects(holdings_to_insert)

    # Aggregate logic
    db.flush()

    aggregates = db.query(
        Holding.thirteen_f_id,
        Holding.cusip,
        Holding.issuer_name,
        Holding.class_title,
        func.sum(Holding.value).label("value"),
        func.sum(Holding.shares_or_principal_amount).label("shares_or_principal_amount"),
        Holding.shares_or_principal_amount_type,
        Holding.option_type,
        func.sum(Holding.voting_authority_sole).label("voting_authority_sole"),
        func.sum(Holding.voting_authority_shared).label("voting_authority_shared"),
        func.sum(Holding.voting_authority_none).label("voting_authority_none")
    ).filter(
        Holding.thirteen_f_id == filing.id
    ).group_by(
        Holding.thirteen_f_id,
        Holding.cusip,
        Holding.issuer_name,
        Holding.class_title,
        Holding.shares_or_principal_amount_type,
        Holding.option_type
    ).all()

    aggregate_holdings_to_insert = []
    for agg in aggregates:
        aggregate_holdings_to_insert.append(AggregateHolding(
            thirteen_f_id=agg.thirteen_f_id,
            cusip=agg.cusip,
            issuer_name=agg.issuer_name,
            class_title=agg.class_title,
            value=agg.value,
            shares_or_principal_amount=agg.shares_or_principal_amount,
            shares_or_principal_amount_type=agg.shares_or_principal_amount_type,
            option_type=agg.option_type,
            voting_authority_sole=agg.voting_authority_sole,
            voting_authority_shared=agg.voting_authority_shared,
            voting_authority_none=agg.voting_authority_none,
            created_at=now,
            updated_at=now
        ))

    if aggregate_holdings_to_insert:
        db.bulk_save_objects(aggregate_holdings_to_insert)

    filing.holdings_count_calculated = len(holdings_to_insert)
    filing.holdings_value_calculated = sum([h.value for h in holdings_to_insert if h.value is not None])
    filing.aggregate_holdings_count = len(aggregate_holdings_to_insert)

    db.commit()
