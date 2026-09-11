-- ---------------------------------------------------------------------
-- BSE Monitor :: PostgreSQL schema
-- Generated from bse_monitor/database/models.py (SQLAlchemy metadata).
-- Applied automatically by `python -m bse_monitor.main init-db`; kept in
-- the repo so the schema is reviewable and can be applied by hand/psql.
-- ---------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS public;

CREATE TABLE audit_log (
	id SERIAL NOT NULL, 
	entity_type VARCHAR(32) NOT NULL, 
	entity_id VARCHAR(64), 
	action VARCHAR(32) NOT NULL, 
	actor VARCHAR(64), 
	details JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE companies (
	id SERIAL NOT NULL, 
	bse_code VARCHAR(16), 
	nse_symbol VARCHAR(32), 
	isin VARCHAR(12), 
	name VARCHAR(512) NOT NULL, 
	normalized_name VARCHAR(512) NOT NULL, 
	sector VARCHAR(128), 
	industry VARCHAR(128), 
	market_cap_cr NUMERIC(18, 2), 
	is_active BOOLEAN NOT NULL, 
	extra JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_companies_normalized_name UNIQUE (normalized_name)
);

CREATE TABLE investors (
	id SERIAL NOT NULL, 
	name VARCHAR(256) NOT NULL, 
	normalized_name VARCHAR(256) NOT NULL, 
	investor_type VARCHAR(32), 
	country VARCHAR(8), 
	is_marquee BOOLEAN NOT NULL, 
	is_tracked BOOLEAN NOT NULL, 
	aliases JSONB, 
	discovered BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE scrape_runs (
	id SERIAL NOT NULL, 
	source VARCHAR(8) NOT NULL, 
	run_started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	run_finished_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(16) NOT NULL, 
	from_date DATE, 
	to_date DATE, 
	records_seen INTEGER, 
	records_new INTEGER, 
	records_duplicate INTEGER, 
	errors INTEGER, 
	checkpoint JSONB, 
	notes TEXT, 
	PRIMARY KEY (id)
);

CREATE TABLE event_filings (
	id SERIAL NOT NULL, 
	company_id INTEGER, 
	source VARCHAR(8) NOT NULL, 
	source_filing_id VARCHAR(128), 
	content_hash VARCHAR(64) NOT NULL, 
	headline TEXT NOT NULL, 
	body_text TEXT, 
	search_text TEXT, 
	category VARCHAR(32) NOT NULL, 
	subcategory VARCHAR(64), 
	secondary_categories JSONB, 
	classification_confidence FLOAT NOT NULL, 
	classification_method VARCHAR(32), 
	classification_evidence JSONB, 
	opportunity_score INTEGER NOT NULL, 
	score_breakdown JSONB, 
	priority VARCHAR(8) NOT NULL, 
	certainty VARCHAR(16), 
	filing_datetime TIMESTAMP WITH TIME ZONE, 
	dissemination_datetime TIMESTAMP WITH TIME ZONE, 
	filing_date DATE, 
	exchange_url TEXT, 
	pdf_url TEXT, 
	pdf_processed BOOLEAN NOT NULL, 
	is_duplicate BOOLEAN NOT NULL, 
	duplicate_of_id INTEGER, 
	raw_payload JSONB, 
	scrape_run_id INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_event_filings_category CHECK (category IN ('IPO', 'FPO', 'QIP', 'RightsIssue', 'OFS', 'BlockDeal', 'InvestorExit', 'PromoterSale', 'FundRaise', 'PreferentialAllotment', 'Other')), 
	CONSTRAINT ck_event_filings_priority CHECK (priority IN ('HIGH', 'MEDIUM', 'LOW')), 
	CONSTRAINT ck_event_filings_source CHECK (source IN ('BSE', 'NSE', 'SEBI', 'IR')), 
	CONSTRAINT ck_event_filings_score_range CHECK (opportunity_score >= 0 AND opportunity_score <= 100), 
	CONSTRAINT uq_filings_source_id UNIQUE (source, source_filing_id), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(duplicate_of_id) REFERENCES event_filings (id) ON DELETE SET NULL, 
	FOREIGN KEY(scrape_run_id) REFERENCES scrape_runs (id) ON DELETE SET NULL
);

CREATE TABLE promoters (
	id SERIAL NOT NULL, 
	company_id INTEGER, 
	name VARCHAR(256) NOT NULL, 
	normalized_name VARCHAR(256) NOT NULL, 
	role VARCHAR(64), 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_promoter_company_name UNIQUE (company_id, normalized_name), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
);

CREATE TABLE daily_alerts (
	id SERIAL NOT NULL, 
	alert_date DATE NOT NULL, 
	filing_id INTEGER, 
	priority VARCHAR(8) NOT NULL, 
	channel VARCHAR(16) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	title TEXT, 
	body TEXT, 
	sent_at TIMESTAMP WITH TIME ZONE, 
	error TEXT, 
	dedupe_key VARCHAR(96) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_alerts_priority CHECK (priority IN ('HIGH', 'MEDIUM', 'LOW')), 
	FOREIGN KEY(filing_id) REFERENCES event_filings (id) ON DELETE CASCADE
);

CREATE TABLE filing_documents (
	id SERIAL NOT NULL, 
	filing_id INTEGER NOT NULL, 
	url TEXT NOT NULL, 
	local_path TEXT, 
	sha256 VARCHAR(64), 
	byte_size INTEGER, 
	page_count INTEGER, 
	extraction_method VARCHAR(32), 
	ocr_used BOOLEAN NOT NULL, 
	text_chars INTEGER, 
	extracted_text TEXT, 
	tables JSONB, 
	download_status VARCHAR(16), 
	error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_document_filing_url UNIQUE (filing_id, url), 
	FOREIGN KEY(filing_id) REFERENCES event_filings (id) ON DELETE CASCADE
);

CREATE TABLE filing_investors (
	filing_id INTEGER NOT NULL, 
	investor_id INTEGER NOT NULL, 
	mention_context TEXT, 
	confidence FLOAT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (filing_id, investor_id), 
	FOREIGN KEY(filing_id) REFERENCES event_filings (id) ON DELETE CASCADE, 
	FOREIGN KEY(investor_id) REFERENCES investors (id) ON DELETE CASCADE
);

CREATE TABLE filing_promoters (
	filing_id INTEGER NOT NULL, 
	promoter_id INTEGER NOT NULL, 
	mention_context TEXT, 
	confidence FLOAT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (filing_id, promoter_id), 
	FOREIGN KEY(filing_id) REFERENCES event_filings (id) ON DELETE CASCADE, 
	FOREIGN KEY(promoter_id) REFERENCES promoters (id) ON DELETE CASCADE
);

CREATE TABLE transactions (
	id SERIAL NOT NULL, 
	filing_id INTEGER NOT NULL, 
	company_id INTEGER, 
	investor_id INTEGER, 
	promoter_id INTEGER, 
	transaction_type VARCHAR(32) NOT NULL, 
	direction VARCHAR(8), 
	security_type VARCHAR(32), 
	num_shares NUMERIC(20, 2), 
	price_per_share NUMERIC(18, 4), 
	amount_inr NUMERIC(20, 2), 
	percent_of_equity FLOAT, 
	issue_size_inr NUMERIC(20, 2), 
	lock_in_expiry_date DATE, 
	board_meeting_date DATE, 
	record_date DATE, 
	currency VARCHAR(8), 
	extraction_confidence FLOAT, 
	extra JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_transactions_type CHECK (transaction_type IN ('PRIMARY_ISSUE', 'SECONDARY_SALE', 'PLEDGE', 'LOCK_IN_EXPIRY', 'BUYBACK', 'OTHER')), 
	FOREIGN KEY(filing_id) REFERENCES event_filings (id) ON DELETE CASCADE, 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(investor_id) REFERENCES investors (id) ON DELETE SET NULL, 
	FOREIGN KEY(promoter_id) REFERENCES promoters (id) ON DELETE SET NULL
);

CREATE INDEX ix_audit_log_entity_id ON audit_log (entity_id);
CREATE INDEX ix_audit_log_entity_type ON audit_log (entity_type);
CREATE UNIQUE INDEX ix_companies_bse_code ON companies (bse_code);
CREATE INDEX ix_companies_isin ON companies (isin);
CREATE INDEX ix_companies_name_lower ON companies (normalized_name);
CREATE INDEX ix_companies_normalized_name ON companies (normalized_name);
CREATE INDEX ix_companies_nse_symbol ON companies (nse_symbol);
CREATE UNIQUE INDEX ix_investors_normalized_name ON investors (normalized_name);
CREATE INDEX ix_scrape_runs_source ON scrape_runs (source);
CREATE INDEX ix_event_filings_category ON event_filings (category);
CREATE INDEX ix_event_filings_company_id ON event_filings (company_id);
CREATE UNIQUE INDEX ix_event_filings_content_hash ON event_filings (content_hash);
CREATE INDEX ix_event_filings_filing_date ON event_filings (filing_date);
CREATE INDEX ix_event_filings_filing_datetime ON event_filings (filing_datetime);
CREATE INDEX ix_event_filings_opportunity_score ON event_filings (opportunity_score);
CREATE INDEX ix_event_filings_priority ON event_filings (priority);
CREATE INDEX ix_event_filings_source_filing_id ON event_filings (source_filing_id);
CREATE INDEX ix_filings_cat_score_date ON event_filings (category, opportunity_score, filing_date);
CREATE INDEX ix_promoters_company_id ON promoters (company_id);
CREATE INDEX ix_promoters_normalized_name ON promoters (normalized_name);
CREATE INDEX ix_daily_alerts_alert_date ON daily_alerts (alert_date);
CREATE UNIQUE INDEX ix_daily_alerts_dedupe_key ON daily_alerts (dedupe_key);
CREATE INDEX ix_daily_alerts_filing_id ON daily_alerts (filing_id);
CREATE INDEX ix_filing_documents_filing_id ON filing_documents (filing_id);
CREATE INDEX ix_filing_documents_sha256 ON filing_documents (sha256);
CREATE INDEX ix_transactions_board_meeting_date ON transactions (board_meeting_date);
CREATE INDEX ix_transactions_company_id ON transactions (company_id);
CREATE INDEX ix_transactions_filing_id ON transactions (filing_id);
CREATE INDEX ix_transactions_lock_in_expiry_date ON transactions (lock_in_expiry_date);

-- Full-text search over the classifier's input blob. Kept out of the ORM
-- because it is PostgreSQL-specific; harmless to skip on other backends.
CREATE INDEX IF NOT EXISTS ix_event_filings_search_fts
    ON event_filings USING gin (to_tsvector('english', coalesce(search_text, '')));

-- Partial index that powers the sell-down and fund-raise pipeline queries,
-- which always filter to non-duplicate, actionable rows.
CREATE INDEX IF NOT EXISTS ix_event_filings_actionable
    ON event_filings (filing_date DESC, opportunity_score DESC)
    WHERE is_duplicate = false AND category <> 'Other';

-- Lock-in expiries are queried forward over a rolling horizon.
CREATE INDEX IF NOT EXISTS ix_transactions_lockin_forward
    ON transactions (lock_in_expiry_date)
    WHERE lock_in_expiry_date IS NOT NULL;
