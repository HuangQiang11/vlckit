#!/usr/bin/env python3
"""从固定源码构建 iOS arm64 VLCKit；所有中间数据归本轮临时目录，只有最终库和验证摘要保留。"""

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request


VLC_REVISION = "005e69e67a8730f128e44bde68437fbb048cf45f"
SOURCE_ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=6, help="编译并发数，默认 6")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs 必须为正整数")
    for tool in ("git", "xcodebuild", "xcrun", "pkg-config", "clang"):
        if not shutil.which(tool):
            parser.error("缺少构建工具：" + tool)

    # TemporaryDirectory 只删除本次创建的精确目录；不使用或清理用户共享 DerivedData／下载缓存。
    with tempfile.TemporaryDirectory(prefix="vido-vlckit-build-") as temporary:
        work = Path(temporary)
        print("构建临时目录：", work, flush=True)
        env = os.environ.copy()
        env["PATH"] = str(work / "toolchain/bin") + os.pathsep + env.get("PATH", "")
        env["PATH"] += ":/usr/sbin:/sbin"
        aclocal = [str(p) for p in (Path("/opt/homebrew/share/aclocal"), Path("/usr/local/share/aclocal")) if p.is_dir()]
        env["ACLOCAL_PATH"] = os.pathsep.join(aclocal + ([env["ACLOCAL_PATH"]] if env.get("ACLOCAL_PATH") else []))

        def run(command, directory, phase):
            """每个阶段输出独立日志；失败先读取诊断，再由外层清理临时目录。"""
            log = work / (phase + ".log")
            print("执行：", phase, flush=True)
            with log.open("a") as output:
                result = subprocess.run([str(v) for v in command], cwd=directory, env=env,
                                        stdout=output, stderr=subprocess.STDOUT)
            if result.returncode:
                print("\n".join(log.read_text(errors="replace").splitlines()[-100:]), flush=True)
                raise RuntimeError(phase + " 失败，退出码 " + str(result.returncode))
            return log

        def fetch(name, url, revision):
            """仅获取远端已有提交并 detached checkout；应用补丁使用 git apply，不创建任何提交。"""
            target = work / name
            target.mkdir()
            run(["git", "init", "-q"], target, "fetch-" + name)
            run(["git", "remote", "add", "origin", url], target, "fetch-" + name)
            run(["git", "fetch", "--depth", "1", "origin", revision], target, "fetch-" + name)
            run(["git", "checkout", "--detach", "FETCH_HEAD"], target, "fetch-" + name)
            return target

        # 复制当前 fork 工作树，包含尚未提交的补丁；不在用户源码目录生成编译数据。
        kit = work / "vlckit"
        def ignore_generated(directory, names):
            ignored = {".git", "build", ".build", "DerivedData"}
            if Path(directory) == SOURCE_ROOT / "libvlc":
                ignored.add("vlc")
            return set(names) & ignored
        shutil.copytree(SOURCE_ROOT, kit, ignore=ignore_generated)
        kit_revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE_ROOT, text=True).strip()
        vlc = fetch("vlc", "https://github.com/videolan/vlc.git", VLC_REVISION)
        for patch in sorted((kit / "libvlc/patches").glob("*.patch")):
            run(["git", "apply", "--whitespace=nowarn", patch], vlc, "apply-upstream-patches")
        patch = kit / "libvlc/patches/0013-hls-inherit-target-duration-and-validate-gap-updates.patch"
        if not patch.is_file():
            raise RuntimeError("缺少 HLS 修复补丁")
        (kit / "libvlc/vlc").symlink_to(vlc, target_is_directory=True)

        # Make 4.4 的 FIFO jobserver 可被嵌套 Ninja 共享；系统 Make 3.81 在此构建链会出现失效描述符。
        archive = work / "make-4.4.1.tar.gz"
        with urllib.request.urlopen("https://ftp.gnu.org/gnu/make/make-4.4.1.tar.gz", timeout=30) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        run(["tar", "-xzf", archive], work, "make-extract")
        make_source = work / "make-4.4.1"
        run(["./configure", "--prefix=" + str(work / "toolchain")], make_source, "make-configure")
        run(["/usr/bin/make", "-j" + str(args.jobs)], make_source, "make-build")
        run(["/usr/bin/make", "install"], make_source, "make-install")
        env["MAKE"] = str(work / "toolchain/bin/make")
        env["MAKEFLAGS"] = "--jobserver-style=fifo"
        python = shutil.which("python3.11") or shutil.which("python3")
        (work / "toolchain/bin/python3").symlink_to(python)
        env["PATH"] = str(vlc / "extras/tools/build/bin") + os.pathsep + env["PATH"]
        run(["./bootstrap"], vlc / "extras/tools", "tools-bootstrap")
        run([env["MAKE"], "-j" + str(args.jobs)], vlc / "extras/tools", "tools-build")
        run(["./bootstrap"], vlc, "vlc-bootstrap")

        # 原生回归只构建核心与 adaptive_test，不生成插件缓存或启动播放器界面。
        native = work / "native"
        native.mkdir()
        help_text = subprocess.check_output([str(vlc / "configure"), "--help"], text=True, env=env)
        keep = {"shared", "static", "libtool-lock", "option-checking", "dependency-tracking", "largefile", "debug", "tests"}
        features = set(re.findall(r"--(?:enable|disable)-([a-z0-9-]+)", help_text))
        flags = ["--disable-" + name for name in sorted(features - keep)]
        env["PKG_CONFIG_LIBDIR"] = ""
        run([vlc / "configure"] + flags + ["--enable-adaptive", "--enable-debug", "--without-contrib"], native, "native-configure")
        run(["./config.status", "libtool"], native, "native-libtool")
        run([env["MAKE"], "-C", "compat", "-j" + str(args.jobs)], native, "native-compat")
        run([env["MAKE"], "-C", "src", "libvlccore.la", "-j" + str(args.jobs)], native, "native-core")
        run([env["MAKE"], "-C", "modules", "adaptive_test", "-j" + str(args.jobs)], native, "native-adaptive")
        test_log = run(["./modules/adaptive_test"], native, "native-tests")
        cases = [line for line in test_log.read_text().splitlines() if line.startswith("Testing ")]
        if len(cases) != 13 or "/* #undef NDEBUG */" not in (native / "config.h").read_text():
            raise RuntimeError("原生测试数量或断言配置不符合预期")
        shutil.rmtree(native)

        # 未构建的平台／架构只提供拒绝编译的私有占位头；XCFramework 仅声明实际生成的两个 arm64 切片。
        project = (kit / "VLCKit.xcodeproj/project.pbxproj").read_text()
        for name in set(re.findall(r"vlc-plugins-[A-Za-z0-9_-]+\.h", project)):
            (kit / "Headers/Internal" / name).write_text('#error "This slice is not included in the iOS arm64 build"\n')

        frameworks = []
        symbols = []
        for sdk, platform, kind in (("iphonesimulator", "iOS Simulator", "simulator"), ("iphoneos", "iOS", "device")):
            build = vlc / ("build-" + sdk + "-arm64")
            build.mkdir()
            run([vlc / "extras/package/apple/build.sh", "--arch=arm64", "--sdk=" + sdk, "-j" + str(args.jobs)], build, sdk + "-core")
            if "/* #undef NDEBUG */" not in (build / "build/config.h").read_text():
                raise RuntimeError(sdk + " 意外关闭了断言，不能据此验证修复")
            install = vlc / ("install-iphone-" + kind)
            install.mkdir()
            shutil.copy2(build / "static-lib/libvlc-full-static.a", install / ("libvlc-" + kind + "-static.a"))
            shutil.copy2(build / "static-lib/static-module-list.c", kit / ("Headers/Internal/vlc-plugins-iphone-" + kind + "-arm64.h"))
            target = work / (sdk + ".xcarchive")
            run(["xcodebuild", "archive", "-project", "VLCKit.xcodeproj", "-scheme", "VLCKit", "-configuration", "Debug",
                 "-sdk", sdk, "-destination", "generic/platform=" + platform, "-archivePath", target,
                 "-derivedDataPath", work / (sdk + "-wrapper"), "ARCHS=arm64", "ONLY_ACTIVE_ARCH=NO",
                 "IPHONEOS_DEPLOYMENT_TARGET=17.0", "SKIP_INSTALL=NO", "CODE_SIGNING_ALLOWED=NO"], kit, sdk + "-wrapper")
            frameworks.append(target / "Products/Library/Frameworks/VLCKit.framework")
            symbols.append(target / "dSYMs/VLCKit.framework.dSYM")
            # 本轮精确构建路径；归档完成后即可释放该切片的源依赖与中间对象，降低双平台构建峰值。
            installed_static = target / "Products/usr/local"
            if installed_static.exists():
                shutil.rmtree(installed_static)
            shutil.rmtree(build)
            shutil.rmtree(install)
            shutil.rmtree(vlc / "contrib" / ("arm64-" + sdk))
            for source_build in (vlc / "contrib").glob("contrib-arm64-apple-*"):
                shutil.rmtree(source_build)
            shutil.rmtree(work / (sdk + "-wrapper"))

        output = work / "VLCKit.xcframework"
        command = ["xcodebuild", "-create-xcframework"]
        for framework, symbol in zip(frameworks, symbols):
            command += ["-framework", framework, "-debug-symbols", symbol]
        run(command + ["-output", output], work, "package-xcframework")
        artifacts = SOURCE_ROOT / "build/Patched-iOS"
        artifacts.mkdir(parents=True, exist_ok=True)
        destination = artifacts / output.name
        if destination.exists():
            shutil.move(str(destination), work / "previous-VLCKit.xcframework")
        try:
            shutil.move(str(output), destination)
        except BaseException:
            previous = work / "previous-VLCKit.xcframework"
            if previous.exists():
                shutil.move(str(previous), destination)
            raise
        summary = ["VLCKit source base: " + kit_revision, "VLC: " + VLC_REVISION,
                   "Patch SHA-256: " + hashlib.sha256(patch.read_bytes()).hexdigest(), "Assertions: enabled"] + cases + ["Native regression exit status: 0"]
        (artifacts / "build-summary.txt").write_text("\n".join(summary) + "\n")
        shutil.copy2(SOURCE_ROOT / "COPYING", destination / "COPYING.txt")
        (destination / "SOURCE.txt").write_text("https://github.com/HuangQiang11/vlckit\n" + "\n".join(summary[:3]) + "\n")
        archive = artifacts / "VLCKit-iOS-arm64.zip"
        run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", destination, archive], SOURCE_ROOT, "zip-release")
        digest = hashlib.sha256()
        with archive.open("rb") as content:
            for block in iter(lambda: content.read(1024 * 1024), b""):
                digest.update(block)
        checksum = digest.hexdigest()
        (artifacts / "checksum.txt").write_text(checksum + "\n")
        print("已生成：", destination, flush=True)
        print("Release ZIP：", archive, flush=True)
        print("Swift Package checksum：", checksum, flush=True)


if __name__ == "__main__":
    main()
