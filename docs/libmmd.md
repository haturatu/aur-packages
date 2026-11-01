# libmmd の配布と更新

`libmmd` は GitHub Release のソースを使う通常の AUR パッケージです。`libmmd-git` ではなく、リリース版を追跡する `libmmd-cpp` という名前で配布しています。

`-git` だと利用者がその時点の最新コミットを取得してビルドするため、再現性のあるライブラリの配布には少し向きません。なので、こちらは**リリース版を追跡して、利用側のアプリがビルドできることも確認してから公開する**構成にしました。

## インストールする

```sh
yay -S libmmd-cpp
```

ヘッダーと CMake パッケージを含めて `/usr` 以下へインストールします。物理機能とシステムの Bullet を有効にし、ビルド時のソース自動取得は無効化しています。

mikumikudesu、pmxer、vmd-trace 側では、次のようにシステムへインストールした libmmd を指定できます。

```sh
cmake -S . -B build/system -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DLIBMMD_PROVIDER=SYSTEM
cmake --build build/system --parallel
```

各アプリ固有の依存関係やアセットは別途必要です。`LIBMMD_PROVIDER=AUTO` は互換性のあるシステム版を優先し、`BUNDLED` はアプリごとに固定されたソースを使います。`vmd-trace` を `VMDTRACE_WITH_MODEL=OFF` でビルドする場合は libmmd を使用しません。

## リリースから AUR へ反映するまで

バージョンを更新しただけで公開すると、依存先の ABI を壊した変更に気付かない場合があります。そのため、`libmmd` の単体テストに加えて、主要な利用側アプリでもビルドを確認します。

流れは次のとおりです。

1. `libmmd` の `CMakeLists.txt` と `include/mmd/version.hpp` のバージョンを更新する。
2. `libmmd` のテストと Package Workflow を通す。
3. 既定ブランチに取り込み、対応する `vX.Y.Z` タグを push する。
4. Release Workflow が検証後に `libmmd-X.Y.Z.tar.gz` を GitHub Release へ公開する。
5. このリポジトリの6時間ごとの Workflow が Release を検知し、`pkgver`、チェックサム、`.SRCINFO` を更新する。
6. 使い捨て Arch Linux コンテナでパッケージをビルド・テストし、`pacman -U` でインストールして利用側アプリを `SYSTEM` でビルドする。
7. すべての必須検証に成功した場合だけ、AUR に通常の Git push で公開する。

### 利用側の検証範囲

現在は mikumikudesu の実行ファイルとコア・CLI テスト、pmxer の CLI 構成、vmd-trace のモデル対応 CLI 構成を対象にしています。PMX / VMD、アニメーション、libmmd 単体の物理テストや動的リンクも確認します。

ただし、GUI 描画、動画キャプチャ、MNN、MediaPipe の動作までは含めていません。**CLI がビルドできたからといって、すべての表示や推論が動くことまで保証するわけではありません。**

vmd-trace は非公開リポジトリです。GitHub の `CONSUMERS_TOKEN` に読み取り専用トークンを設定すると、ランナーが取得したソースを読み取り専用でコンテナへ渡して検証できます。

トークンやソースが取得できない場合は、その利用先のテストを警告付きでスキップする構成です。全3アプリの検証に成功した場合と、2アプリだけを確認できた場合は区別して扱ってください。

すぐに確認したい場合は、Workflow を `dry_run=true` で手動実行できます。実際に公開する場合は `dry_run=false` と、`aur-production` の `AUR_SSH_PRIVATE_KEY` が必要です。

## ローカルで検証する

まずレシピの更新とビルドを試します。

```sh
./aurctl update libmmd-cpp
./aurctl validate libmmd-cpp --build
./aurctl publish libmmd-cpp --dry-run
```

利用側アプリを含む検証は、ホスト環境を書き換えないよう使い捨ての Arch Linux コンテナで実行してください。

```sh
docker run --rm -v "$PWD:/repo" \
  archlinux:base-devel bash /repo/scripts/ci-validate.sh
```

非公開のソースまで検証する場合は、**コンテナの外で clone したものを読み取り専用で渡す**構成です。認証情報をコンテナの中へ渡さないようにします。

```sh
git clone --depth 1 https://github.com/haturatu/vmd-trace.git \
  .cache/consumers/vmd-trace
docker run --rm -v "$PWD:/repo" \
  archlinux:base-devel bash /repo/scripts/ci-validate.sh
```

上の clone はリポジトリを取得できる権限がある場合だけ成功します。また、この Docker コマンドだけで外部ソースが自動的に認識されるとは限りません。利用側のソースの配置先と `LIBMMD_CONSUMER_DIR` を検証スクリプトに合わせて確認してください。

### パッケージ名について

AUR の `libmmd` は別プロジェクトが過去に使用して削除された名前で、採用には Package Maintainer の承認が必要でした。そのため、このライブラリは `libmmd-cpp` という名前で公開しています。

インストールされるファイル名、SONAME、CMake パッケージ名は upstream のまま `libmmd` です。利用側は `libmmd-cpp` をインストールし、`find_package(libmmd)` で検出します。

## ABI が変わる場合

`0.3` 系の SONAME は `.so.0.3` です。互換性のあるパッチ更新なら同じ SONAME を維持しますが、ABI を壊す場合はマイナーバージョンと SONAME の更新が必要です。

その場合は3つのアプリの CMake 要件や組み込み版も更新して再ビルドします。利用側の必須検証が失敗している間は AUR に自動公開されません。
