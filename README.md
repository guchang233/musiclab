# MusicLab

音乐工作站内核：**音源多轨分离** + **WAV→MIDI 转录**引擎。

采用分层、可插拔架构：核心能力沉淀于 engine 层，UI 与 AI 推理解耦，
后续功能（变调、降噪、和弦识别等）通过统一插件接口扩展。

## 架构

```
UI / CLI ──► pipeline（编排 + 内容寻址缓存）──► engine（分离 / 转录后端）
                                                    │
                                                    ├─ ML 后端（按需安装）
                                                    │    ├─ demucs（Stem 分离）
                                                    │    └─ basic-pitch（转录）
                                                    └─ DSP 降级后端（零依赖）
                                                         ├─ spectral HPSS（分离）
                                                         └─ pyin + onset（转录）
```

- **分层**：UI / pipeline / engine 三层，核心逻辑独立可测。
- **模型解耦**：后端通过 `BackendSpec` + 能力矩阵注册，
  依赖可选，缺失时自动降级到 DSP 后端并给出明确提示。
- **内容寻址缓存**：以「步骤名 + 后端 + 归一化参数 + 音频摘要 SHA256」为键，
  重复处理未变化音频时直接命中缓存，大幅提速。

## 安装

```bash
pip install -e .            # 核心（含 DSP 降级后端，可直接用）
pip install -e .[all]       # 启用 ML 后端（demucs + basic-pitch）
```

## 使用

```bash
# 分轨：将歌曲拆成 bass / drums / chords / other
musiclab separate in.wav -o out/

# 转谱：单条音频 → MIDI
musiclab transcribe melody.wav -o melody.mid --instrument piano

# 一键管线：分轨 → 逐轨转录
musiclab pipeline song.wav -o out/
```

## 测试

```bash
pytest -q     # 合成音频 fixtures，环境无关
```