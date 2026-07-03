# Discord RSS Notifier（GitHub Actions版）

N8N不要・自前サーバー不要・Windowsタスクスケジューラ不要。
一回セットアップしたら、あとはGitHub側のインフラが勝手に30分おきに起動して
RSSの新着記事をDiscordへ投稿し続ける。パパのPCが起動していなくても動く。

## セットアップ手順（一回だけ）

1. **GitHubで新規リポジトリを作る**
   - リポジトリ名は何でもいい（例: `discord-rss-notifier`）
   - Private推奨（中身を人に見せる必要はないので）
   - READMEなしで作成してOK（このフォルダの中身をそのままpushする）

2. **このフォルダの中身を丸ごとpushする**
   ```
   cd github_actions_repo
   git init
   git add .
   git commit -m "init: discord rss notifier"
   git branch -M main
   git remote add origin https://github.com/izumoitachi-gif/discord-rss-notifier.git
   git push -u origin main
   ```
   （リポジトリ名を`discord-rss-notifier`以外にした場合はURLの末尾を合わせて書き換える）

3. **Discord側でWebhook URLを作る**
   - 通知したいDiscordサーバー → チャンネルの設定 → 連携サービス → ウェブフックを作成
   - 表示されたURLをコピー

4. **GitHubリポジトリにWebhook URLを登録する**
   - リポジトリの `Settings` → `Secrets and variables` → `Actions`
   - `New repository secret` をクリック
   - Name: `DISCORD_WEBHOOK_URL`
   - Secret: さっきコピーしたWebhook URL
   - `Add secret`

5. **動作確認（任意）**
   - リポジトリの `Actions` タブ → `Discord RSS Notifier` → `Run workflow` で手動実行できる
   - 実行ログを見て、投稿が成功しているか確認する

これで完了。以後は`.github/workflows/notify.yml`の設定通り、
GitHub側が30分おきに自動で`notifier.py`を実行し続ける。
パパは何もしなくていい。

## ソースを増やしたい時

`notifier.py`の`RSS_SOURCES`辞書に1行足して、`git add . && git commit -m "add source" && git push`
するだけ。次の巡回から自動で反映される。

## 仕組みメモ

- GitHub Actionsの`schedule`トリガーが30分おきにワークフローを起動する
  （GitHub側のインフラ上で実行される。パパのPCは一切関与しない）
- 起動のたびに新しい使い捨て環境（Ubuntu runner）が立ち上がり、
  リポジトリをcheckoutしてPythonスクリプトを1回だけ実行して終了する
- 「前回どこまで投稿したか」の記録（`seen_urls.json`）は、
  実行のたびにリポジトリへコミット＆プッシュして書き戻すことで、
  次回の実行にも引き継がれるようにしている（＝リポジトリ自体が記憶装置）
