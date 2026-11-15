"""
Makes the key pair the server signs mail commands with (and the mail service checks them with):

    python -m app.mail_signing_key

Put the first line in the server's environment and the second in the mail service's (mail.env). Make it once; keep the private
key out of everything but the server's environment.
"""
import base64

from app.services.mail_bus import new_signing_keypair


def main() -> None:
    private, public = new_signing_keypair()
    print("# server.env")
    print(f"MAIL_COMMAND_SIGNING_KEY={base64.b64encode(private).decode()}")
    print("# mail.env")
    print(f"MAIL_TRUSTED_SERVER_KEY={base64.b64encode(public).decode()}")


if __name__ == "__main__":
    main()
