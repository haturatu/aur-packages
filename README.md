# AUR packages

AUR に公開しているパッケージの PKGBUILD と、更新・検証・公開までの処理をまとめたリポジトリです。管理ブランチは `master` です。

パッケージが少ないうちは AUR の Git リポジトリをそれぞれ更新すればよいのですが、数が増えると同じ作業を繰り返すことになります。かといって、上流に新しいコミットが来るたびに、何も確認せず AUR へ push するのも避けたいところです。

なので、**パッケージの追加方法を共通化し、Arch Linux で実際にビルドできたレシピだけを AUR へ反映する**構成にしています。更新検出には nvchecker、パッケージ処理には makepkg、AUR との同期には通常の Git を使っています。

## 管理しているパッケージ

| PackageBase | 上流 | 更新方法 |
| --- | --- | --- |
| `ahaha-git` | [ahaha](https://github.com/haturatu/ahaha) | 最新の Git ソースを検証 |
| `ght-git` | [ght](https://github.com/haturatu/ght) | 最新の Git ソースを検証 |
| `profile-sync-daemon-openrc-git` | [profile-sync-daemon-openrc](https://github.com/haturatu/profile-sync-daemon-openrc) | Git の最新版から OpenRC 用パッケージをビルド |
| `libmmd-cpp` | [libmmd](https://github.com/haturatu/libmmd) | GitHub Release の安定版を追跡 |

`-git` の付いた VCS パッケージは、利用者が `makepkg` を実行するときに最新ソースを取得し、`pkgver()` でバージョンを決めます。上流のコミットが増えただけでは、AUR に `pkgver` の更新を push しません。

依存関係やビルド方法を変えた場合は、必要に応じて `pkgrel` を増やします。一方、`libmmd-cpp` はリリース版なので、新しい GitHub Release に合わせて `pkgver`、チェックサム、`.SRCINFO` を更新します。

## なぜビルドと公開を分けているのか

自動更新で怖いのは、レシピを書き換えたあと、ビルドできるか分からないまま AUR に公開してしまうことです。また、ビルド時に実行される PKGBUILD へ、公開用の SSH 鍵を渡したくもありません。

そこで [.github/workflows/aur.yml](.github/workflows/aur.yml) では、検証と公開を別のジョブにしています。

1. `aurctl` のユニットテストとシェルの検査を行う。
2. **公開用 SSH 鍵を持たない**使い捨ての Arch Linux コンテナで、更新確認と全パッケージのビルド・`check()` を実行する。
3. 検証済みレシピと設定ファイルのハッシュを成果物に保存する。
4. 別の公開ジョブで AUR と比較し、差分があるレシピだけ通常の Git push で公開する。
5. 更新したレシピと AUR の同期状態を `master` に保存する。

公開ジョブで PKGBUILD を実行することはありません。また、検証に使用した SHA がまだ `master` にあることも確認します。公開途中で AUR 側に別の変更が入っていた場合は、勝手に上書きせず停止します。

### 定期実行と手動実行

定期実行は6時間ごとで、UTC の 00:23 / 06:23 / 12:23 / 18:23、日本時間では 09:23 / 15:23 / 21:23 / 翌 03:23 です。

それ以外に `master` への push と手動実行でも起動します。PR ではテストと検証だけを行い、AUR へは公開しません。

公開せず結果を確かめたい場合は、手動実行の `dry_run` を使います。既定は `true` です。

```sh
gh workflow run aur.yml --repo haturatu/aur-packages -f dry_run=true
gh run list --repo haturatu/aur-packages --workflow aur.yml
```

実際に AUR への公開も行う場合は、`dry_run=false` を指定します。

```sh
gh workflow run aur.yml --repo haturatu/aur-packages -f dry_run=false
```

通常の定期実行と `master` push も公開ジョブまで進みます。ドキュメントだけの変更でも Workflow が起動するので、公開用 Environment と Secret の状態を確認してください。

## ローカルで確認する

`aurctl` は Python 3.11 以降の標準ライブラリで動作します。CLI 自体に外部 Python パッケージは必要ありません。

ただしレシピを更新・検証する場合は、Arch Linux の `makepkg`、`namcap`、`nvchecker`、`pacman-contrib` とビルド依存関係が必要です。`makepkg` は root ではなく一般ユーザーで実行してください。

```sh
./aurctl list
./aurctl check --all
./aurctl update --all
./aurctl validate --all --build
./aurctl publish --all --dry-run
```

`./aurctl deps --all` で事前に必要な依存パッケージも確認できます。`validate` はホストへパッケージを自動インストールしません。

CI に近い使い捨て環境で試すなら Docker から実行します。

```sh
docker run --rm -v "$PWD:/repo" \
  archlinux:base-devel bash /repo/scripts/ci-validate.sh
```

**注意:** このスクリプトは、コンテナ内の一般ユーザーが作業できるよう、マウントしたチェックアウトの所有者を一時変更します。終了時には元へ戻しますが、普段使っている重要な作業ツリーではなく、専用の clone を使う方が安全です。

ビルド中の `pkgver()` による変更や生成物は、一時ディレクトリで処理して管理中のレシピへ戻しません。

## パッケージを追加する

### すでに AUR にある場合

既存のパッケージは AUR の最新 `master` から取り込みます。ローカルのビルド生成物をコピーして管理する方式ではありません。

```sh
./aurctl add example-git
./aurctl validate example-git --build

git add packages configs .aur
git commit -m 'feat: add example-git package'
git push origin master
```

自分が管理しているパッケージをまとめて取り込むこともできます。

```sh
./aurctl import --maintainer haturatu
```

すでに登録済みのパッケージは変更しません。取り込み後は `configs/<pkgbase>.toml` を確認してください。`libmmd-cpp` のような安定版には `[nvchecker]` の設定が必要です。

### 新しいパッケージを作る場合

新規レシピの雛形も生成できます。

```sh
./aurctl add example-git --template vcs --upstream owner/example
./aurctl add example --template github-release --upstream owner/example
```

ただし、生成しただけで公開可能になるわけではありません。初期状態の `package()` は意図的に失敗し、`package.publish` も無効です。

PKGBUILD の説明、ライセンス、依存関係、`build()`、`check()`、`package()` を上流に合わせて修正し、安定版なら実在するバージョンとチェックサムを設定します。

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

`register` は AUR 側に同名パッケージがないことを確認し、登録済みの AUR 用 SSH 鍵で最初のコミットを送信します。`configs/<pkgbase>.toml` の `package.publish = true` に切り替えるのは、レシピを仕上げてからです。

最初の登録以外は通常の更新処理が使えます。新しいパッケージを追加しても Workflow を個別に書き換える必要はありません。

## 安定版の更新を追跡する

GitHub Release などの最新版検出には nvchecker を使います。パッケージごとの `configs/<pkgbase>.toml` に次のように設定します。

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

`aurctl` は取得したバージョンを `vercmp` で比較します。新しい場合だけ `pkgver` を更新し、`pkgrel=1` に戻した後、`updpkgsums` と `makepkg --printsrcinfo` を実行します。

ダウングレードや検出失敗の場合は停止します。`pkgver=` と `pkgrel=` はそれぞれトップレベルの1行で宣言し、ソース URL からは `$pkgver` を参照してください。

nvchecker には GitLab、PyPI、JSON、正規表現などの取得方法もあります。ただし、上流のディレクトリ構成やビルド方法が変わった場合まで自動で対応できるわけではありません。そのときはレシピ自体を修正して再検証します。

## AUR との競合をどう扱うのか

公開に使う SSH 鍵は、GitHub Environment `aur-production` の `AUR_SSH_PRIVATE_KEY` に設定します。鍵の内容はリポジトリに保存せず、SSH のホスト鍵は [aur_known_hosts](.github/aur_known_hosts) で固定しています。

AUR の履歴を残すため、強制 push は使っていません。取り込んだ時点と現在の AUR のレシピが違っていれば公開を停止します。

例えば別のメンテナが AUR 側を修正していた場合、GitHub のレシピでその変更を消してしまわないようにするためです。AUR 側の変更を確認し、必要な内容をこちらへ取り込んでから再公開します。

対象の `packages/<pkgbase>`、`configs/<pkgbase>.toml`、`.aur/<pkgbase>.json` を退避し、`aurctl add <pkgbase>` で最新の AUR レシピを再取り込みしてから差分を統合する方法もあります。

AUR への push 自体が成功した後に GitHub 側の保存が失敗しても、次回の実行で同一のレシピを見つけたら同期状態を回復します。一部だけ公開できた場合も同様です。

## どこまで検証しているのか

使い捨ての Arch Linux コンテナでソース検証、ビルド、`check()`、namcap の診断を行います。ただし **`devtools` のクリーン chroot をパッケージごとに作る方式ではありません**。

`profile-sync-daemon-openrc-git` については OpenRC の実行環境を用意していないため、サービスの実際の起動や実行時の統合テストまでは行いません。パッケージ作成とビルド・テスト依存関係を確認します。

namcap は診断結果を出しますが、その警告だけで公開を止めることはありません。`ahaha-git` と `ght-git` の `glibc` 依存宣言は過去の検証で追加済みです。`profile-sync-daemon-openrc-git` の `custom:none` というライセンス表記には診断が残りますが、上流に確認できる文書がないため推測で変更していません。

`.SRCINFO` の不一致、ビルド失敗、`check()` の失敗は公開を止めます。

また、GitHub Actions の定期実行は遅延することがあります。公開リポジトリに60日間活動がない場合はスケジュールが無効化されるため、長期に更新がない場合は Actions の状態も確認してください。

## libmmd-cpp の場合

`libmmd-cpp` は `-git` ではない通常の AUR パッケージです。AUR の `libmmd` は別プロジェクトが使用していた名前のため、この名前で公開しています。更新前に生成したパッケージをインストールし、mikumikudesu・pmxer・vmd-trace を `LIBMMD_PROVIDER=SYSTEM` でビルドする検証も行います。

ただし、検証に必要なリポジトリのソースが取得できない場合は、その利用先の確認をスキップする構成です。**すべての利用先で毎回検証できる保証ではありません。**

インストール、リリース手順、ABI の扱いは [libmmd の配布と更新](docs/libmmd.md)にまとめています。

## テストと資料

```sh
python3 -m unittest discover -s tests -v
actionlint
shellcheck scripts/*.sh
```

テストでは一時的な bare Git リポジトリを AUR の代わりに使い、実際のパッケージへ push しません。

実装を分けた理由は [設計資料](docs/design.md)を参照してください。AUR パッケージングの詳細は [VCS パッケージガイドライン](https://wiki.archlinux.jp/index.php/VCS_パッケージガイドライン)、[makepkg(8)](https://man.archlinux.org/man/makepkg.8.en)、[nvchecker](https://nvchecker.readthedocs.io/en/latest/usage.html)にあります。
