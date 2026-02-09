import pymysql

def init_db():
    conn = pymysql.connect(
        host='localhost', 
        user='root', 
        password='19871019', 
        database='chenyiyun',
        charset='utf8mb4'
    )
    cursor = conn.cursor()
    
    with open('web_schema.sql', 'r') as f:
        sql_content = f.read()
        
    commands = sql_content.split(';')
    
    for cmd in commands:
        if cmd.strip():
            try:
                cursor.execute(cmd)
                print(f"Executed: {cmd[:50]}...")
            except Exception as e:
                print(f"Error executing {cmd[:50]}...: {e}")
                
    conn.commit()
    conn.close()
    print("Database initialization complete.")

if __name__ == "__main__":
    init_db()
