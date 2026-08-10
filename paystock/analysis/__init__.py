"""指標計算と判定ルール。

portfolio モジュールは config を import するため、ここでは読み込まない
(config -> analysis.rules -> analysis.__init__ -> portfolio -> config の
循環 import になる)。必要な場合は `from paystock.analysis import portfolio`
のように明示的に import すること。
"""

from . import indicators, rules  # noqa: F401
