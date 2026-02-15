
from sqlalchemy import text
from scoreRank.core.db_io import get_engine

def add_column():
    engine = get_engine()
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE score_rank_daily ADD COLUMN claude_score FLOAT AFTER opt_score"))
            print("Added claude_score column.")
        except Exception as e:
            if "Duplicate column name" in str(e):
                print("Column already exists.")
            else:
                print(f"Error: {e}")

if __name__ == "__main__":
    add_column()
