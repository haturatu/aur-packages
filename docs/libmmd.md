# libmmd の配布と更新

`libmmd` は GitHub Release のソースを使う通常の AUR パッケージです。`-git` パッケージではありません。

## インストール

```sh
yay -S libmmd
```

libmmd のヘッダーと CMake パッケージを含めて `/usr` 以下にインストールします。物理機能とシステムの Bullet を有効にし、ソースの自動取得は無効にします。

mikumikudesu、pmxer、vmd-trace のソースで次を実行すると、インストール済みの libmmd を必須にできます。

```sh
cmake -S . -B build/system -G Ninja -DCMAKE_BUILD_TYPE=Release -DLIBMMD_PROVIDER=SYSTEM
cmake --build build/system --parallel
```

アプリケーション固有のビルド依存関係やアセットも準備してください。`AUTO` は互換性のあるシステム版を優先し、`BUNDLED` は各アプリケーションに固定されたソースを使います。vmd-trace の `VMDTRACE_WITH_MODEL=OFF` では libmmd を使用しません。

## リリース手順

1. libmmd の `CMakeLists.txt` と `include/mmd/version.hpp` のバージョンを更新します。
2. libmmd のテストと Package ワークフローを通します。
3. 変更を既定ブランチへ取り込み、対応する `vX.Y.Z` タグを push します。
4. Release ワークフローが検証後に `libmmd-X.Y.Z.tar.gz` を GitHub Release へ公開します。
5. このリポジトリの6時間ごとのワークフローが Release を検出し、`pkgver`、チェックサム、`.SRCINFO` を更新します。
6. クリーンな Arch Linux コンテナーでビルドとテストを実行します。生成したパッケージを `pacman -U` でインストールし、3アプリケーションの `main` を `SYSTEM` でビルドします。
7. 全検証に成功した場合のみ AUR へ push し、更新レシピと同期状態をこのリポジトリへ保存します。

検証では mikumikudesu の実行ファイルとコア・CLI テスト、pmxer の CLI 構成、vmd-trace のモデル対応 CLI 構成をビルドします。PMX/VMD、アニメーション、ライブラリ単体の物理テストと、実行ファイルの動的リンクを確認します。GUI 描画、動画キャプチャ、MNN、MediaPipe の動作検証は含みません。検証ログには利用したアプリケーションのコミットを出力します。

vmd-trace は非公開リポジトリです。リポジトリシークレット `CONSUMERS_TOKEN` に読み取り専用トークンを設定すると、ランナー上で取得したソースを読み取り専用でコンテナへ渡し、全3アプリケーションを検証します。未設定の場合、その検証は警告を出して省略し、公開リポジトリの2アプリケーションだけで合否を判定します。

すぐに更新する場合は AUR maintenance を `dry_run=false` で手動実行できます。`aur-production` 環境の `AUR_SSH_PRIVATE_KEY` が必要です。

## ローカル検証

```sh
./aurctl update libmmd
./aurctl validate libmmd --build
./aurctl publish libmmd --dry-run
```

3アプリケーションを含む検証は、使い捨ての Arch Linux コンテナーで実行してください。ホストのパッケージを変更するスクリプトではありません。

```sh
docker run --rm -v "$PWD:/repo" archlinux:base-devel bash /repo/scripts/ci-validate.sh
```

非公開リポジトリも含めて検証する場合は、コンテナーの外で取得したソースを置くか、読み取り専用でマウントします。認証情報はコンテナーへ渡しません。

```sh
git clone --depth 1 https://github.com/haturatu/vmd-trace.git .cache/consumers/vmd-trace
docker run --rm -v "$PWD:/repo" archlinux:base-devel bash /repo/scripts/ci-validate.sh
```

初回移行では、削除済みの別プロジェクトの `libmmd` Git 履歴が AUR に残っていました。元のレシピを `aurctl add libmmd` で取り込んだ同期状態から、検証済みレシピを通常の fast-forward push で公開します。以後は通常の `publish` で更新します。

削除済みパッケージは push では復元できません。初回だけ次の手順が必要です。

```sh
ssh aur@aur.archlinux.org restore libmmd
ssh aur@aur.archlinux.org adopt libmmd
```

`restore` はパッケージを孤児として復元し、`adopt` は採用申請を行います。Package Maintainer が承認するまで push は拒否されます。その間の公開ジョブは失敗しますが、レシピと同期状態は変更されません。承認後は次回の定期実行または手動実行で公開されます。

## ABI の変更

0.3 系の SONAME は `.so.0.3` です。互換性のあるパッチ更新では同じ SONAME を維持します。互換性を壊す変更ではマイナーバージョンと SONAME を更新し、3アプリケーションの CMake 要件・組み込み版も更新して再ビルドしてください。アプリケーションとの統合検証が失敗している間は、自動公開されません。
