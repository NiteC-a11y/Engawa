# Engawa テスト（標準ライブラリ unittest のみ・GUI/ネットワーク不要）。
# 実行: リポジトリ直下で  python -m unittest discover -s tests -t . -v
import os
import sys

# リポジトリ直下を import パスに（discover でも単体実行でも acp/sources 等を解決できるように）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
