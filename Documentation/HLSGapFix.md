# HiTV 的 HLS 分片窗口修复

本 fork 在 `libvlc/patches/0013-hls-inherit-target-duration-and-validate-gap-updates.patch` 维护 VLC 内核补丁。沿用构建脚本的 VLC 基线 `005e69e67a8730f128e44bde68437fbb048cf45f`，依次应用原有 12 个补丁及此修复。

## 行为

- HLS 解析器将有效的 `EXT-X-TARGETDURATION` 提供为列表可继承的默认时长；相对时间列表遇到滑动窗口序号缺口时据此估算丢失分片的时间。各分片的 `EXTINF` 时长仍独立保留，小数值不变。
- 转移分片所有权前预检整次时间计算。缺少有效默认时长或计算溢出时保留旧窗口，走正常刷新／播放失败路径，避免 `assert(duration)` 终止进程及部分转移造成重复释放。
- Debug 构建保留断言，不使用禁用断言、runtime hook 或拦截 `abort` 的方式绕过故障。

## 构建与分发

```sh
python3 Packaging/buildPatchedIOS.py --jobs 6
```

此入口面向 HiTV：仅生成 iOS 真机 arm64 和 Apple Silicon 模拟器 arm64，包装层最低系统版本为 iOS 17。不生成 macOS、tvOS、watchOS、visionOS 或 Intel 模拟器版本。

需要完整 Xcode、Git、Python 3、pkg-config 和网络下载依赖。脚本在专用临时目录构建 Make 4.4.1 及其他工具，不替换系统安装；先运行真实 VLC 原生自适应测试，再依次构建两个 iOS 切片。通过 `git apply` 应用补丁，不自动创建 Git 提交。每个切片归档后清理其依赖和中间对象，任务结束时删除本轮临时目录与日志。

最终 XCFramework、dSYM、ZIP、checksum 与构建摘要放在忽略 Git 的 `build/Patched-iOS/`。Release 必须上传该 ZIP，随后将 `Package.swift` 的二进制 URL 和 checksum 同时更新为对应 Release 的值。只把 Swift Package 仓库地址改成此 fork，不会改变仍从官方服务器下载的旧二进制。

App 应固定到本 fork 的发布版本或提交，避免跟随 master 自动引入上游变化。源码补丁、重建脚本和许可证随 fork 保留，二进制通过 Release 分发。

## 验证状态

原生 `adaptive_test` 的 13 组测试入口已通过，包含新增的时长继承、小数保留、正常跳号、缺少默认时长及溢出回归，断言开启。记录见 [HLSGapFix/native-regression.txt](HLSGapFix/native-regression.txt)。该记录针对相同的 VLC 内核基线及补丁，不代表整个 fork 包装层均已经验收。

真机与模拟器的内核、包装层均已归档，修复库的 5 个播放集成实验均通过；详见 [playback-regression.md](HLSGapFix/playback-regression.md)。真机仅完成编译，实际播放交互仍由项目负责人验收。
