---
description: inbox/invoice/ の未処理請求書を請求書処理担当エージェントに処理させる
---

`inbox/invoice/` を確認し、未処理ファイル(`processed/invoice/originals/` にまだ存在しないもの)があれば、Agent ツールで `invoice-processor` サブエージェントを呼び出して処理させてください。未処理ファイルがなければその旨を報告してください。
