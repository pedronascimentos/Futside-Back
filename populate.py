import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Importar os modelos do seu ficheiro main.py
# Isto assume que este script está na mesma pasta que o main.py
from main import User, Locador, Field, get_password_hash

def populate_asa_sul_courts():
    """
    Conecta-se ao banco de dados e insere uma lista de quadras públicas
    localizadas na Asa Sul de Brasília.
    """
    load_dotenv()
    
    SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")
    if not SQLALCHEMY_DATABASE_URL:
        print("ERRO: A variável de ambiente DATABASE_URL não foi encontrada.")
        return

    print("A ligar ao banco de dados...")
    try:
        engine = create_engine(SQLALCHEMY_DATABASE_URL)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()
        print("Ligação bem-sucedida.")
    except Exception as e:
        print(f"ERRO: Não foi possível ligar ao banco de dados: {e}")
        return

    try:
        # Passo 1: Garantir que existe um "locador" para as quadras públicas.
        # Vamos procurar por um utilizador "Prefeitura" ou criar um.
        public_owner_email = "prefeitura@brasilia.df.gov.br"
        owner_user = db.query(User).filter_by(email=public_owner_email).first()

        if not owner_user:
            print(f"A criar um utilizador genérico '{public_owner_email}' para as quadras públicas...")
            owner_user = User(
                name="Prefeitura de Brasília",
                email=public_owner_email,
                hashed_password=get_password_hash("default_password")
            )
            db.add(owner_user)
            db.flush() # Para obter o ID antes de criar o locador
            
            locador_profile = Locador(user_id=owner_user.id)
            db.add(locador_profile)
            db.commit()
            db.refresh(owner_user)
            print(f"Utilizador 'Prefeitura' criado com ID: {owner_user.id}")
        
        owner_locador = db.query(Locador).filter_by(user_id=owner_user.id).first()
        if not owner_locador:
             # Isto não deve acontecer, mas é uma salvaguarda
            raise Exception("Não foi possível encontrar ou criar o perfil de locador para o utilizador da prefeitura.")

        # Passo 2: Lista de quadras públicas na Asa Sul com coordenadas aproximadas
        courts_to_add = [
            {"name": "Quadra da SQS 102", "address": "Superquadra Sul 102, Brasília - DF", "lat": -15.8037, "lng": -47.8829},
            {"name": "Quadra da SQS 103", "address": "Superquadra Sul 103, Brasília - DF", "lat": -15.8062, "lng": -47.8856},
            {"name": "Quadra da SQS 104", "address": "Superquadra Sul 104, Brasília - DF", "lat": -15.8088, "lng": -47.8883},
            {"name": "Quadra da SQS 105", "address": "Superquadra Sul 105, Brasília - DF", "lat": -15.8113, "lng": -47.8910},
            {"name": "Quadra da SQS 202", "address": "Superquadra Sul 202, Brasília - DF", "lat": -15.8030, "lng": -47.8902},
            {"name": "Quadra da SQS 203", "address": "Superquadra Sul 203, Brasília - DF", "lat": -15.8055, "lng": -47.8929},
            {"name": "Quadra da SQS 204", "address": "Superquadra Sul 204, Brasília - DF", "lat": -15.8081, "lng": -47.8956},
            {"name": "Quadra da SQS 205", "address": "Superquadra Sul 205, Brasília - DF", "lat": -15.8106, "lng": -47.8983},
            {"name": "Quadra da SQS 402", "address": "Superquadra Sul 402, Brasília - DF", "lat": -15.8023, "lng": -47.8974},
            {"name": "Quadra da SQS 403", "address": "Superquadra Sul 403, Brasília - DF", "lat": -15.8048, "lng": -47.9001},
            {"name": "Parque da Cidade (Quadras)", "address": "Parque da Cidade Sarah Kubitschek, Asa Sul, Brasília - DF", "lat": -15.7996, "lng": -47.9103},
        ]

        print(f"\nA adicionar {len(courts_to_add)} quadras ao banco de dados...")
        
        new_courts_count = 0
        for court_data in courts_to_add:
            # Verifica se uma quadra com o mesmo nome já existe para este locador
            existing_court = db.query(Field).filter_by(name=court_data["name"], locador_id=owner_locador.id).first()
            if not existing_court:
                new_court = Field(
                    locador_id=owner_locador.id,
                    name=court_data["name"],
                    address=court_data["address"],
                    city="Brasilia",
                    state="DF",
                    latitude=court_data["lat"],
                    longitude=court_data["lng"]
                )
                db.add(new_court)
                print(f"- A adicionar: {court_data['name']}")
                new_courts_count += 1
            else:
                print(f"- Já existe: {court_data['name']}. A ignorar.")

        if new_courts_count > 0:
            db.commit()
            print(f"\nOPERAÇÃO CONCLUÍDA: {new_courts_count} novas quadras foram adicionadas com sucesso.")
        else:
            print("\nNenhuma quadra nova para adicionar.")

    except Exception as e:
        print(f"\nERRO durante a operação: {e}")
        db.rollback()
    finally:
        db.close()
        print("Ligação com o banco de dados fechada.")


if __name__ == "__main__":
    confirm = input("Este script irá popular o banco de dados com quadras públicas da Asa Sul. Deseja continuar? (s/n): ")
    if confirm.lower() == 's':
        populate_asa_sul_courts()
    else:
        print("Operação cancelada.")
