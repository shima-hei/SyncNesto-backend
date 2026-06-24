"""初期管理者seedの互換エントリーポイント。"""

import logging

from scripts.seed_rbac import seed_rbac

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed_rbac()
