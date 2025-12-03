# app.py
import os
import sqlite3
import certifi
import warnings
import logging
import traceback
import pandas as pd
from urllib.parse import quote_plus
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine, inspect, text
from urllib3.exceptions import InsecureRequestWarning
import openai
import re 
from dotenv import load_dotenv

# ─── CONFIG & LOGGING ─────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
BASE_DIR   = os.path.dirname(__file__)
CONFIG_DB  = os.path.join(BASE_DIR, "sample.db")  # only used if the file already exists

# Fallback MSSQL server from your SSMS screenshot. Code prefers env/config first.
# DEFAULT_MSSQL_SERVER = r"2S3Q7R3\MSSQLSERVER1"

DEFAULT_MSSQL_SERVER = r"DESKTOP-GSJB5GJ"


os.environ["SSL_CERT_FILE"] = certifi.where()
warnings.filterwarnings("ignore", category=InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load env (optional)
load_dotenv("ATT92742.env")

# Note: startup no longer creates/initializes any local DB files or config entries.
# Configuration is read from environment variables first; if sample.db exists, we may read from it (read-only).


def get_config_value(key: str) -> str | None:
    """
    Read configuration value from environment first.
    If not present and sample.db exists, read from its config table (read-only).
    Do NOT create or modify sample.db here.
    """
    # 1) environment variable (highest priority)
    val = os.getenv(key)
    if val is not None and val != "":
        return val

    # 2) sample.db (only if file exists) - read-only
    if os.path.exists(CONFIG_DB):
        try:
            conn = sqlite3.connect(CONFIG_DB)
            cur = conn.cursor()
            cur.execute("SELECT value FROM config WHERE key = ?", (key,))
            row = cur.fetchone()
            conn.close()
            return row[0] if row else None
        except Exception as e:
            logger.debug(f"Failed to read {key} from sample.db: {e}")
            return None

    # 3) not found
    return None


def get_db_mode() -> str:
    # priority: env -> sample.db (if present) -> default mssql
    return (get_config_value("DB_MODE") or "mssql").lower()


# ─── FLASK SETUP ───────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)


# ─── SAFE IDENTIFIERS (basic check to avoid injection in identifiers) ──────────
def _safe_identifier(name: str) -> str:
    """
    Allow only basic alphanumeric + underscore characters for table/database names.
    Raises ValueError for anything unsafe.
    """
    # if not name or not re.match(r'^[A-Za-z0-9_]+$', name):
    #     raise ValueError("Invalid identifier. Only A-Z, a-z, 0-9 and underscore allowed.")
    return name


def _build_mssql_conn_str(db_name: str | None = None) -> str:
    """
    Build a SQLAlchemy pyodbc connection string for MSSQL.
    Prefers environment variables; if missing falls back to DEFAULT_MSSQL_SERVER.
    """
    server = get_config_value("MSSQL_SERVER") or DEFAULT_MSSQL_SERVER
    default_db = db_name or (get_config_value("MSSQL_DB") or "master")
    user = get_config_value("MSSQL_USER") or None
    pwd  = get_config_value("MSSQL_PASS") or None

    # Optional driver override
    driver_name = get_config_value("MSSQL_DRIVER") or "ODBC Driver 17 for SQL Server"

    if user and pwd:
        odbc_str = (
            f"DRIVER={{{driver_name}}};"
            f"SERVER={server};DATABASE={default_db};UID={user};PWD={pwd};"
        )
    else:
        odbc_str = (
            f"DRIVER={{{driver_name}}};"
            f"SERVER={server};DATABASE={default_db};Trusted_Connection=yes;"
        )

    return "mssql+pyodbc:///?odbc_connect=" + quote_plus(odbc_str)


def connect_to_server(db_name: str | None = None):
    """
    Returns a SQLAlchemy engine:
      - mssql mode -> engine connected to MSSQL server 'master' (or configured default DB)
      - sqlite fallback -> engine pointed at existing CONFIG_DB (only if present)
    No database creation or modification is performed here.
    """
    mode = get_db_mode()
    if mode == "mssql":      
        
        conn_str = _build_mssql_conn_str(db_name)
        
        engine = create_engine(conn_str, pool_timeout=30, pool_recycle=3600)
        # basic smoke test
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as e:
            logger.error(f"MS SQL connect test failed: {e}")
            raise
        logger.info(f"Connected to MSSQL server { (get_config_value('MSSQL_SERVER') or DEFAULT_MSSQL_SERVER) } (db=master)")
        return engine

    # sqlite fallback only if file exists
    if os.path.exists(CONFIG_DB):
        uri = f"sqlite:///{CONFIG_DB}"
        engine = create_engine(uri, connect_args={"check_same_thread": False})
        return engine

    raise RuntimeError("No valid database configuration found (DB_MODE not 'mssql' and sample.db missing)")


def connect_to_database(db_name: str):
    """
    Return engine to a specific database name:
      - mssql: returns engine targetting the requested database name on the server
      - sqlite: returns engine if CONFIG_DB exists
    """
    mode = get_db_mode()
    if mode == "mssql":
        _safe_identifier(db_name)
        conn_str = _build_mssql_conn_str(db_name)
        engine = create_engine(conn_str, pool_timeout=30, pool_recycle=3600)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as e:
            logger.error(f"MS SQL connect to DB '{db_name}' failed: {e}")
            raise
        logger.info(f"Connected to MSSQL database: {db_name}")
        return engine

    # sqlite fallback only if file exists
    if os.path.exists(CONFIG_DB):
        return connect_to_server()

    raise RuntimeError("SQLite DB not found and DB_MODE is not mssql.")

 
# ─── ROUTES ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    try:
        return render_template("index.html")
    except Exception:
        return "Row count validation API is running."


@app.route("/api/databases", methods=["GET"])
def get_databases_endpoint():
    """
    For mssql mode: return server databases.
    For sqlite fallback: return filename as logical DB.
    """
    try:
        mode = get_db_mode()
        if mode == "mssql":
            engine = connect_to_server()
            query = "SELECT name FROM sys.databases WHERE database_id > 4 AND state = 0"
            with engine.connect() as conn:
                df = pd.read_sql(query, conn)
            return jsonify(df['name'].tolist())
        else:
            return jsonify([os.path.basename(CONFIG_DB)])
    except Exception as e:
        logger.error(f"Error fetching databases: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/tables", methods=["POST"])
def get_tables_endpoint():
    """
    For mssql: given a database name (posted), return its table names.
    For sqlite fallback: list tables from sqlite_master (only if file exists).
    """
    try:
        data = request.get_json()
        if not data or "database" not in data:
            return jsonify({"error": "Database name is required"}), 400

        mode = get_db_mode()
        posted_db = data["database"]

        if mode == "mssql":
            engine = connect_to_database(posted_db)
            inspector = inspect(engine)
            # Get all schemas
            schemas = inspector.get_schema_names()
            # Fetch tables for each schema
            all_tables = []
            for schema in schemas:
                for table in inspector.get_table_names(schema=schema):
                    all_tables.append(f"{schema}.{table}")

            return jsonify(all_tables)
        else:
            if not os.path.exists(CONFIG_DB):
                return jsonify({"error": "SQLite DB not available"}), 500
            engine = connect_to_server()
            query = """
              SELECT name
                FROM sqlite_master
               WHERE type = 'table'
                 AND name NOT LIKE 'sqlite_%'
               ORDER BY name
            """
            df = pd.read_sql(query, engine)
            return jsonify(df["name"].tolist())

    except Exception as e:
        logger.error(f"Error fetching tables: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/columns", methods=["POST"])
def get_columns_endpoint():
    """
    Given {"table": "<table_name>"} return column list for that table.
    """
    try:
        data = request.get_json()
        table = data.get("table")
        if not table:
            return jsonify({"error": "table is required"}), 400
        engine = connect_to_server()
        inspector = inspect(engine)
        cols = inspector.get_columns(table)
        return jsonify([c["name"] for c in cols])
    except Exception as e:
        logger.error(f"Error fetching columns: {e}")
        return jsonify({"error": str(e)}), 500

def generate_where_clause(
    prompt: str,
    source_db: str,
    source_table: str,
    source_col
    
) -> str:
    """
    Generate a dynamic, type-safe SQL WHERE clause.
    Handles:
    - 'any column' with type-safe IN clauses
    - BETWEEN conditions
    - String/date vs numeric type handling
    - GPT fallback for complex free-form conditions
    """

    try:
        # -------- GPT fallback for complex prompts -------- #
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.debug("OPENAI_API_KEY not found. Using fallback WHERE 1=1.")
            return "WHERE 1=1"

        openai.api_key = api_key

        system_prompt = "You are an expert SQL assistant."
        
        user_prompt = f"""
        Generate ONLY a valid SQL query (no explanation, no code fences, no extra text) that returns the row count for the specified table.
        - The query MUST start with: SELECT COUNT(*) AS row_count FROM {source_db}.{source_table}
        - If a filtering condition can be inferred from the user's natural-language condition, include a WHERE clause. If the condition is ambiguous or cannot be interpreted, use: WHERE 1=1
        - Use these inputs only: source_db: {source_db}, source_table: {source_table}, source_columns: {', '.join([col['name'] for col in source_col])}
        - If the user says "any column" (or similar), match across ALL source_columns joined with OR and wrapped in parentheses.
        - Do NOT reference columns not present in source_columns; ignore unknown column names.
        - Translate common NL patterns:
        - "between A and B" → `col BETWEEN A AND B`
        - comma-separated lists or "in (a, b, c)" → `col IN (v1, v2, ...)`
        - "starts with X" → `col LIKE 'X%'`
        - "ends with X" → `col LIKE '%X'`
        - "contains X" → `col LIKE '%X%'`
        - "is null" / "is not null" → `col IS NULL` / `col IS NOT NULL`
        - comparisons (>, <, >=, <=, =, !=) → use standard SQL operators
        - Literals:
        - Wrap string and date values in single quotes, escaping single quotes by doubling them.
        - Do NOT quote numeric or boolean literals.
        - Assume common date format YYYY-MM-DD if ambiguous; wrap in quotes.
        - Case-insensitive requests: use LOWER(column) and lower the literal (e.g., `LOWER(col) LIKE '%x%'`).
        - When combining OR conditions, group them with parentheses: `(colA = 'x' OR colB = 'x')`.
        - Output should be a properly formatted multi-line SQL statement (line breaks allowed) and end with a single semicolon.
        - Do NOT include any comments, hints, or metadata—only the SQL statement.

        Examples:
        User: "status = active and created between 2023-01-01 and 2023-03-31"
        →
        SELECT COUNT(*) AS row_count
        FROM {source_db}.{source_table}
        WHERE status = 'active' AND created BETWEEN '2023-01-01' AND '2023-03-31';

        User: "any column contains foo and id in (1,2,3)"
        →
        SELECT COUNT(*) AS row_count
        FROM {source_db}.{source_table}
        WHERE (col1 LIKE '%foo%' OR col2 LIKE '%foo%' OR col3 LIKE '%foo%') AND id IN (1,2,3);

        Now produce the SQL query for this user condition:
        "{prompt}"
        """



        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0,
            max_tokens=200
        )
        wc = response.choices[0].message.content.strip()
        # if not wc.lower().startswith("where"):
        #     wc = f"WHERE {wc}"

        # Remove unsupported table prefixes
        wc = re.sub(r'\b(source|target)\.', '', wc, flags=re.IGNORECASE)
        print("Generated Query:", wc)
        return wc

    except Exception as e:
        print(f"generate_where_clause error: {e}")
        return "WHERE 1=1"

def get_row_count(engine, table_name, where_clause):
    """
    Use DB-specific quoting for table name:
      - mssql : [table]
      - sqlite: "table"
    """
    mode = get_db_mode()
    _safe_identifier(table_name)
    if mode == "mssql":
        q = where_clause
    else:
        q = where_clause
    with engine.connect() as conn:
        result = conn.execute(text(q))
        val = result.scalar()
        return int(val or 0)

def generate_simple_summary(source_count, target_count, is_anomaly):
    if source_count == target_count:
        return f"Perfect match: both have {source_count} rows."
    diff = abs(source_count - target_count)
    pct = (diff / max(source_count, target_count)) * 100 if max(source_count, target_count) else 0
    status = "significant discrepancy" if pct > 5 else "minor difference"
    anomaly = " Possible anomaly." if is_anomaly else ""
    return f"Row count mismatch: {diff} rows difference ({pct:.1f}% {status}).{anomaly}"


def generate_summary(source_count, target_count, source_query,target_query):
    """
    Uses OpenAI GPT-4o-mini to generate a data quality summary.
    Falls back to generate_simple_summary if API key not set or error occurs.
    """
    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.debug("OPENAI_API_KEY not found. Using fallback summary.")
            return generate_simple_summary(source_count, target_count)

        openai.api_key = api_key

        system_prompt = "You are a data quality analyst. Write clear, concise summaries for data engineering reports."
        user_prompt = f"""
        Analyze the following row count validation details and write a clear 4–5 sentence summary for a data engineering report:

        - Source Row Count: {source_count}
        - Target Row Count: {target_count}
        - Filter Applied (Source Query): {source_query}
        - Filter Applied (target Query): {target_query}
        """

        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0,
            max_tokens=250
        )

        summary = response.choices[0].message.content.strip()
        return summary

    except Exception as e:
        logger.error(f"OpenAI GPT summary error: {e}")
        return generate_simple_summary(source_count, target_count)

def split_schema_table(full_table_name):
    if '.' in full_table_name:
        parts = full_table_name.split('.', 1)
        return parts[0], parts[1]  # schema, table
    else:
        return 'dbo', full_table_name

# ─── VALIDATE ENDPOINT ─────────────────────────────────────────────────────────
@app.route("/api/validate", methods=["POST"])
def validate():
    try:
        data = request.get_json()
        for field in ['source_db', 'target_db', 'source_table', 'target_table', 'prompt']:
            if not data.get(field):
                return jsonify({"error": f"Missing field: {field}"}), 400

        source_db = data['source_db']
        target_db = data['target_db']
        source_table = data['source_table']
        target_table = data['target_table']
        prompt = data['prompt']

        # --- Split schema and table ---
        source_schema, source_table_name = split_schema_table(source_table)
        target_schema, target_table_name = split_schema_table(target_table)

        # --- Get source columns ---
        engine = connect_to_server(source_db)
        inspector = inspect(engine)
        source_cols = inspector.get_columns(source_table_name, schema=source_schema)
        Source_SQL_Query = generate_where_clause(prompt, source_db, source_table,source_cols)
        src_engine = connect_to_database(source_db) 
        src_count = get_row_count(src_engine, source_table, Source_SQL_Query)
        print("source values",source_db,source_table, Source_SQL_Query)
        print("source count",src_count)
        # --- Get target columns ---
        engine = connect_to_server(target_db)
        inspector = inspect(engine)
        target_cols = inspector.get_columns(target_table_name, schema=target_schema)
        Target_SQL_Query = generate_where_clause(prompt, target_db, target_table,target_cols)
        tgt_engine = connect_to_database(target_db)
        tgt_count = get_row_count(tgt_engine, target_table, Target_SQL_Query)
        print("target values",target_db,target_table, Target_SQL_Query)
        print("target count",tgt_count)

        summary = generate_summary(src_count, tgt_count, Source_SQL_Query,Target_SQL_Query)

        return jsonify({
            "source_count": src_count,
            "target_count": tgt_count,
            "Source_Query": Source_SQL_Query,
            "target_Query": Target_SQL_Query,

            "summary": summary
        })

    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Validation failed: {str(e)}"}), 500


# ─── ERROR HANDLERS & MAIN ─────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500


if __name__ == '__main__':
    logger.info(f"Starting app in DB_MODE={get_db_mode()}")
    # Accept DEFAULT_MSSQL_SERVER as valid fallback:
    if get_db_mode() == "mssql" and not (get_config_value("MSSQL_SERVER") or DEFAULT_MSSQL_SERVER):
        print("Please set MSSQL_SERVER in environment if you do not want to use the default fallback server.")
    else:
        app.run(debug=True, host='0.0.0.0', port=5000)