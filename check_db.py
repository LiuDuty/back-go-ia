import sqlite3

def check_db():
    try:
        conn = sqlite3.connect('c:/IA/back-go-ia/conversas.db')
        c = conn.cursor()
        
        with open('db_dump.txt', 'w', encoding='utf-8') as f:
            f.write("--- Configurações ---\n")
            c.execute("SELECT * FROM configuracoes")
            configs = c.fetchall()
            for conf in configs:
                f.write(f"ID: {conf[0]}\nNome: {conf[1]}\nConteúdo: {conf[2]}\n\n")
                
        conn.close()
        print("Dump concluído.")
    except Exception as e:
        print(f"Erro: {e}")

if __name__ == "__main__":
    check_db()
