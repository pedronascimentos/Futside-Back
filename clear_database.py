import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

def clear_all_tables():
    """
    Connects to the database and drops all known tables.
    This is a destructive operation and should only be used in development.
    """
    # Carrega as variáveis de ambiente (especialmente a DATABASE_URL)
    load_dotenv()
    
    SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")
    if not SQLALCHEMY_DATABASE_URL:
        print("ERRO: A variável de ambiente DATABASE_URL não foi encontrada.")
        print("Certifique-se de que o arquivo .env está na mesma pasta e configurado corretamente.")
        return

    print(f"Conectando ao banco de dados...")
    try:
        engine = create_engine(SQLALCHEMY_DATABASE_URL)
        connection = engine.connect()
        print("Conexão bem-sucedida.")
    except Exception as e:
        print(f"ERRO: Não foi possível conectar ao banco de dados: {e}")
        return

    # Comandos SQL para apagar as tabelas na ordem correta para evitar erros de dependência.
    # A cláusula CASCADE cuida das dependências automaticamente.
    sql_commands = [
        "DROP TABLE IF EXISTS player_match CASCADE;",
        "DROP TABLE IF EXISTS user_region_subscription CASCADE;",
        "DROP TABLE IF EXISTS match CASCADE;",
        "DROP TABLE IF EXISTS field CASCADE;",
        "DROP TABLE IF EXISTS locador CASCADE;",
        "DROP TABLE IF EXISTS user_player_profile CASCADE;",
        "DROP TABLE IF EXISTS field_reservation CASCADE;",
        "DROP TABLE IF EXISTS field_operating_hours CASCADE;",
        # CORREÇÃO: A palavra "user" é reservada no SQL e deve estar entre aspas duplas.
        'DROP TABLE IF EXISTS "user" CASCADE;'
    ]

    print("\nIniciando a exclusão das tabelas...")
    
    try:
        # Usando 'with' para garantir que a conexão seja fechada mesmo se ocorrer um erro.
        with engine.connect() as connection:
            # Usando uma transação para garantir que todos os comandos sejam executados ou nenhum.
            with connection.begin() as transaction:
                for command in sql_commands:
                    connection.execute(text(command))
                    # Extrai o nome da tabela de forma mais segura
                    table_name = command.split(" ")[4].replace('"', '').replace(';','')
                    print(f"- Tabela {table_name} excluída com sucesso.")
            # O 'commit' é feito automaticamente ao sair do bloco 'with' da transação
            print("\nOPERAÇÃO CONCLUÍDA: Todas as tabelas foram excluídas.")
    except Exception as e:
        print(f"\nERRO durante a exclusão das tabelas: {e}")
        # O rollback é feito automaticamente se uma exceção ocorrer dentro do 'with' da transação
        print("A transação foi revertida.")
    finally:
        # A conexão é fechada automaticamente pelo 'with engine.connect() as connection:'
        print("Conexão com o banco de dados fechada.")


if __name__ == "__main__":
    # Pede uma confirmação final para o usuário por segurança
    confirm = input("ATENÇÃO: Esta ação apagará TODAS as tabelas do banco de dados.\nIsso não pode ser desfeito. Deseja continuar? (s/n): ")
    if confirm.lower() == 's':
        clear_all_tables()
    else:
        print("Operação cancelada.")
