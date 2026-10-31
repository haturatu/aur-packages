# AUR packages

`haturatu` が管理する AUR パッケージのレシピと自動検証をまとめたリポジトリです。管理ブランチは `master` です。

GitHub Actions は 6 時間ごとに最新版のソースを取得し、Arch Linux 上でソース検証、ビルド、`check()`、namcap の診断を実行します。全件のビルドに成功すると、AUR と差分があるレシピだけを公開します。

## 管理パッケージ

| PackageBase | 上流 | 更新方針 |
| --- | --- | --- |
| `ahaha-git` | [haturatu/ahaha](https://github.com/haturatu/ahaha) | 最新 Git ソースを定期ビルド |
| `ght-git` | [haturatu/ght](https://github.com/haturatu/ght) | 最新 Git ソースを定期ビルド |
| `profile-sync-daemon-openrc-git` | [haturatu/profile-sync-daemon-openrc](https://github.com/haturatu/profile-sync-daemon-openrc) | 最新 Git ソースからサービスのパッケージを作成 |

VCS パッケージでは、上流コミットの追加だけを理由に `pkgver` を AUR にコミットしません。利用者の `makepkg` が最新版を取得して `pkgver()` を実行します。依存関係やビルド方法などを変更した場合は、必要に応じて `pkgrel` を増やしてください。

## 自動処理

ワークフローは `.github/workflows/aur.yml` です。

1. 共通 CLI のテストを実行します。
2. 認証情報を渡さない Arch Linux コンテナで、安定版の更新確認と全パッケージのフルビルドを行います。
3. 検証したレシピとそのハッシュを、別の公開ジョブへ渡します。
4. AUR の現在のファイルを比較し、差分があるパッケージを通常の Git push で公開します。
5. レシピと AUR の同期状態を `master` に保存します。

定期実行は UTC の 00:23、06:23、12:23、18:23、日本時間の 03:23、09:23、15:23、21:23 です。`master` への push と手動実行でも起動します。PR では検証だけを実行します。手動実行の `dry_run` は既定で有効です。

```sh
gh workflow run aur.yml --repo haturatu/aur-packages -f dry_run=true
gh run list --repo haturatu/aur-packages --workflow aur.yml
```

公開を含めて手動実行する場合は `-f dry_run=false` を指定します。

## ローカルで確認する

共通 CLI 自体は Python 3.11 以降の標準ライブラリだけで動作します。レシピの検証・更新には Arch Linux の `makepkg`、`namcap`、`nvchecker`、`pacman-contrib` と、各パッケージのビルド依存関係が必要です。`makepkg` は一般ユーザーで実行してください。

```sh
./aurctl list
./aurctl check --all
./aurctl update --all
./aurctl validate --all --build
./aurctl publish --all --dry-run
```

`./aurctl deps --all` は、事前にインストールする依存パッケージ名を出力します。`validate` はホストにパッケージを自動インストールしません。CI と同じ使い捨て環境で検証する場合は、Docker から実行できます。

```sh
docker run --rm -v "$PWD:/repo" archlinux:base-devel bash /repo/scripts/ci-validate.sh
```

このコマンドはコンテナ内の一般ユーザーが作業できるよう、マウントしたチェックアウトの所有者を変更します。実行には専用のチェックアウトを使用してください。ビルド時の `pkgver` 書き換えや生成物は一時ディレクトリに置かれ、管理中のレシピには残りません。

## 既存 AUR パッケージを追加する

既存パッケージは AUR の最新 `master` から取り込みます。ローカルのビルド生成物は取り込みません。

```sh
./aurctl add example-git
./aurctl validate example-git --build
git add packages configs .aur
git commit -m 'feat: add example-git package'
git push origin master
```

メンテナのパッケージをまとめて取り込むこともできます。既に管理しているパッケージは変更しません。

```sh
./aurctl import --maintainer haturatu
```

取り込み後に `configs/<pkgbase>.toml` を確認してください。安定版には `[nvchecker]` の設定が必要です。

## 新しい AUR パッケージを作成する

```sh
./aurctl add example-git --template vcs --upstream owner/example
./aurctl add example --template github-release --upstream owner/example
```

テンプレートは公開無効の状態で生成されます。`PKGBUILD` の説明、ライセンス、依存関係、`build()`、`check()`、`package()` を上流に合わせて実装してください。初期状態の `package()` は失敗するため、未完成のレシピは公開できません。

レシピを完成させたら `configs/<pkgbase>.toml` の `package.publish` を `true` に変更します。安定版は実在するバージョンとチェックサムも設定し、`.SRCINFO` を生成します。

```sh
cd packages/example-git
makepkg --printsrcinfo > .SRCINFO
cd ../..
./aurctl validate example-git --build
./aurctl register example-git --dry-run
./aurctl register example-git
git add packages configs .aur
git commit -m 'feat: add example-git package'
git push origin master
```

初回の `register` は、AUR に登録済みの SSH 鍵を使用し、空の AUR Git リポジトリへ最初のコミットを送信します。同名のパッケージが存在する場合は停止します。以後は通常の定期公開が使えます。新規作成、既存パッケージの追加とも、ワークフローの編集は不要です。

## 安定版の更新設定

`configs/<pkgbase>.toml` に、nvchecker の設定を記述します。

```toml
[package]
kind = "release"
publish = true

[nvchecker]
source = "github"
github = "owner/example"
use_latest_release = true
prefix = "v"
```

`aurctl` は nvchecker の検出結果を `vercmp` で比較し、新しい場合だけ `pkgver` を更新して `pkgrel=1` に戻します。その後、`updpkgsums` と `makepkg --printsrcinfo` を実行します。ダウングレードや検出失敗では停止します。バージョンはトップレベルの `pkgver=`、`pkgrel=` 各 1 行で記述し、ソース URL では `$pkgver` を参照してください。

GitLab、PyPI、JSON、正規表現なども nvchecker の `[nvchecker]` 設定で扱えます。上流ソースの切り替えやビルド手順の変更は、レシピを手動で修正して検証してください。

## 公開認証と競合への対処

GitHub Environment `aur-production` の Secret `AUR_SSH_PRIVATE_KEY` に AUR 専用鍵を設定します。現在はローカルの既存 AUR 専用鍵 `aur_deploy` を設定しています。鍵の値はリポジトリに保存しません。SSH のホスト鍵は `.github/aur_known_hosts` で固定します。

公開ジョブは `PKGBUILD` を実行しません。フルビルド済みのファイルと設定のハッシュが一致した場合だけ公開します。AUR の履歴は保持し、強制 push は使用しません。`.aur/<pkgbase>.json` の取り込み時点のファイルと AUR の現在のファイルが異なる場合は、自動上書きを停止します。

競合が発生した場合は AUR の最新コミットとレシピを確認し、GitHub 側へ必要な変更を取り込んでください。AUR 側のファイルを基準に同期状態を更新する必要があるため、対象の `packages/<pkgbase>`、`configs/<pkgbase>.toml`、`.aur/<pkgbase>.json` を退避してから `aurctl add <pkgbase>` で再取り込みし、差分を統合する方法が使えます。

AUR への push 後に GitHub への保存が失敗しても、次の実行で AUR と同一のレシピを検出して同期状態を回復します。一部パッケージの公開後に失敗した場合も同様です。

## 検証範囲

`profile-sync-daemon-openrc-git` は OpenRC 向けのサービスファイルを配布します。Arch Linux CI では OpenRC の実行環境を用意せず、ソース検証とパッケージ作成を確認します。すべてのビルド・テスト依存関係は確認しますが、サービスの起動試験と実行時依存関係の統合試験は対象外です。この例外は当該パッケージの設定と CI ログに明記しています。

namcap は診断結果をログに出力します。上流のテストが `check()` にある場合は `makepkg` が実行します。現在の CI は使い捨ての Arch コンテナで動作し、`devtools` によるパッケージ単位のクリーン chroot とは異なります。

GitHub の定期実行には遅延があり、公開リポジトリに 60 日間活動がない場合は無効化されます。長期に変更がない場合は Actions の有効状態を確認し、必要に応じて再有効化してください。

## テストと設計資料

```sh
python3 -m unittest discover -s tests -v
actionlint
shellcheck scripts/*.sh
```

テストは一時的な bare Git リポジトリを AUR の代わりに使い、実パッケージへは push しません。実装の選択理由は [設計](docs/design.md) を参照してください。

## 参考資料

- [ArchWiki: VCS パッケージガイドライン](https://wiki.archlinux.jp/index.php/VCS_パッケージガイドライン)
- [makepkg(8)](https://man.archlinux.org/man/makepkg.8.en)
- [nvchecker の設定と JSON ログ](https://nvchecker.readthedocs.io/en/latest/usage.html)
- [GitHub Actions の定期実行](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
