import os
from datetime import datetime, date
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Text, Numeric, Boolean, Date, DateTime, ForeignKey, Index
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.types import JSON

Base = declarative_base()

class AggregateHolding(Base):
    __tablename__ = 'aggregate_holdings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    thirteen_f_id = Column(BigInteger, nullable=False)
    cusip = Column(Text, nullable=False)
    issuer_name = Column(Text)
    class_title = Column(Text)
    value = Column(Numeric)
    shares_or_principal_amount = Column(Numeric)
    shares_or_principal_amount_type = Column(Text)
    option_type = Column(Text)
    voting_authority_sole = Column(BigInteger)
    voting_authority_shared = Column(BigInteger)
    voting_authority_none = Column(BigInteger)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('index_aggregate_holdings_on_cusip_and_thirteen_f_id', 'cusip', 'thirteen_f_id'),
        Index('index_aggregate_holdings_on_thirteen_f_id', 'thirteen_f_id'),
    )

class CusipSymbolMapping(Base):
    __tablename__ = 'cusip_symbol_mappings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    cusip = Column(Text, nullable=False, unique=True)
    symbol = Column(Text)
    name = Column(Text)
    exchange = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

class Holding(Base):
    __tablename__ = 'holdings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    thirteen_f_id = Column(BigInteger, nullable=False)
    cusip = Column(Text, nullable=False)
    issuer_name = Column(Text)
    class_title = Column(Text)
    value = Column(Numeric)
    shares_or_principal_amount = Column(Numeric)
    shares_or_principal_amount_type = Column(Text)
    option_type = Column(Text)
    investment_discretion = Column(Text)
    other_manager = Column(Text)
    voting_authority_sole = Column(BigInteger)
    voting_authority_shared = Column(BigInteger)
    voting_authority_none = Column(BigInteger)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('index_holdings_on_cusip_and_thirteen_f_id', 'cusip', 'thirteen_f_id'),
        Index('index_holdings_on_thirteen_f_id', 'thirteen_f_id'),
    )

class ThirteenF(Base):
    __tablename__ = 'thirteen_fs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(Text, nullable=False, unique=True)
    cik = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    form_type = Column(Text, nullable=False)
    directory_url = Column(Text, nullable=False)
    date_filed = Column(Date, nullable=False)
    report_date = Column(Date)
    street1 = Column(Text)
    street2 = Column(Text)
    city = Column(Text)
    state_or_country = Column(Text)
    zip_code = Column(Text)
    other_included_managers_count = Column(Integer)
    holdings_count_reported = Column(Integer)
    holdings_count_calculated = Column(Integer)
    holdings_value_reported = Column(Numeric)
    holdings_value_calculated = Column(Numeric)
    confidential_omitted = Column(Boolean)
    filing_year = Column(Integer, nullable=False)
    filing_quarter = Column(Integer, nullable=False)
    report_year = Column(Integer)
    report_quarter = Column(Integer)
    other_managers = Column(JSON, nullable=False, default=list)
    primary_doc_url = Column(Text)
    info_table_url = Column(Text)
    primary_doc_xml = Column(Text)
    info_table_xml = Column(Text)
    xml_data_fetched_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    report_type = Column(Text)
    amendment_type = Column(Text)
    amendment_number = Column(Integer)
    file_number = Column(Text)
    restated_by_id = Column(BigInteger)
    aggregate_holdings_count = Column(Integer)

    __table_args__ = (
        Index('index_thirteen_fs_on_amendment_type', 'amendment_type'),
        Index('index_thirteen_fs_on_cik_and_report_date', 'cik', 'report_date'),
        Index('index_thirteen_fs_on_date_filed', 'date_filed'),
        Index('index_thirteen_fs_on_report_date', 'report_date'),
        Index('index_thirteen_fs_on_year_quarter_restated', 'report_year', 'report_quarter', 'restated_by_id'),
        Index('index_thirteen_fs_on_restated_by_id', 'restated_by_id'),
    )

# Create a sqlite database for testing/verification.
# We'll use an in-memory or a local file depending on requirements.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./thirteen_f.db")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
