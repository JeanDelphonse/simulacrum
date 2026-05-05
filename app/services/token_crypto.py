from flask import current_app


def encrypt_token(token: str) -> str:
    from cryptography.fernet import Fernet
    key = current_app.config.get('ENCRYPTION_KEY')
    if not key:
        return token
    fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    from cryptography.fernet import Fernet
    key = current_app.config.get('ENCRYPTION_KEY')
    if not key:
        return encrypted_token
    fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return fernet.decrypt(encrypted_token.encode()).decode()
