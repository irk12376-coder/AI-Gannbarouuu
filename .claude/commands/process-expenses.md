---
description: inbox/expense/ の未処理領収書・経費申請を経費精算担当エージェントに処理させる
---

`inbox/expense/` を確認し、未処理ファイル(`processed/expense/originals/` にまだ存在しないもの)があれば、Agent ツールで `expense-report-processor` サブエージェントを呼び出して処理させてください。未処理ファイルがなければその旨を報告してください。
