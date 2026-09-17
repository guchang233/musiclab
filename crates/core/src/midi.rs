//! MIDI 文件写出（SMF 格式 0，手工字节级）。

use std::io::Write;
use std::path::Path;

use crate::engine::Note;
use crate::error::Result;

/// 每四分音符 tick 数。
pub const PPQ: u32 = 480;

fn varlen(v: u32, out: &mut Vec<u8>) {
    let mut buf = [0u8; 5];
    let mut idx = buf.len() - 1;
    buf[idx] = (v & 0x7f) as u8;
    let mut v = v >> 7;
    while v > 0 {
        idx -= 1;
        buf[idx] = ((v & 0x7f) | 0x80) as u8;
        v >>= 7;
    }
    out.extend_from_slice(&buf[idx..]);
}

fn sec_to_tick(sec: f32, bpm: f32) -> u32 {
    let beats = sec * bpm / 60.0;
    (beats * PPQ as f32).round().max(0.0) as u32
}

/// 写出格式 0 MIDI（单轨；鼓用 channel 9）。
pub fn write_midi(path: impl AsRef<Path>, notes: &[Note], bpm: f32, drum: bool) -> Result<()> {
    let channel = if drum { 9 } else { 0 };
    let mut track: Vec<(u32, u8, Vec<u8>)> = Vec::with_capacity(notes.len() * 2);
    for n in notes {
        let on = sec_to_tick(n.onset, bpm);
        let off = sec_to_tick(n.onset + n.dur, bpm).max(on + 1);
        track.push((
            on,
            1,
            vec![0x90 | channel, n.midi.min(127), n.vel.clamp(1, 127)],
        ));
        track.push((off, 0, vec![0x80 | channel, n.midi.min(127), 64]));
    }
    track.sort_by(|a, b| (a.0, a.1).cmp(&(b.0, b.1)));

    let mut ev = Vec::new();
    // tempo（µs per quarter）
    let mpqn = (60_000_000.0 / bpm).round() as u32;
    varlen(0, &mut ev);
    ev.extend_from_slice(&[0xff, 0x51, 0x03]);
    ev.extend_from_slice(&mpqn.to_be_bytes()[1..4]);
    let mut last_tick = 0u32;
    for (tick, _, bytes) in &track {
        varlen(tick - last_tick, &mut ev);
        last_tick = *tick;
        ev.extend_from_slice(bytes);
    }
    varlen(0, &mut ev);
    ev.extend_from_slice(&[0xff, 0x2f, 0x00]); // end of track

    let mut file = Vec::new();
    file.extend_from_slice(b"MThd");
    file.extend_from_slice(&6u32.to_be_bytes());
    file.extend_from_slice(&0u16.to_be_bytes()); // format 0
    file.extend_from_slice(&1u16.to_be_bytes()); // 1 track
    file.extend_from_slice(&(PPQ as u16).to_be_bytes());
    file.extend_from_slice(b"MTrk");
    file.extend_from_slice(&(ev.len() as u32).to_be_bytes());
    file.extend_from_slice(&ev);

    let mut f = std::fs::File::create(path.as_ref())?;
    f.write_all(&file)?;
    f.sync_all()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn midi_bytes_wellformed() {
        let notes = vec![
            Note {
                onset: 0.0,
                dur: 0.5,
                midi: 60,
                vel: 100,
            },
            Note {
                onset: 0.5,
                dur: 0.25,
                midi: 62,
                vel: 90,
            },
        ];
        let p = std::env::temp_dir().join("mlcore-midi-test.mid");
        write_midi(&p, &notes, 120.0, false).unwrap();
        let b = std::fs::read(&p).unwrap();
        assert_eq!(&b[0..4], b"MThd");
        assert_eq!(&b[4..8], &6u32.to_be_bytes());
        // format 0 / 1 track / PPQ 480
        assert_eq!(&b[8..10], &[0x00, 0x00]);
        assert_eq!(&b[10..12], &[0x00, 0x01]);
        assert_eq!(&b[12..14], &(PPQ as u16).to_be_bytes());
        assert_eq!(&b[14..18], b"MTrk");
        let mtrk_len = u32::from_be_bytes([b[18], b[19], b[20], b[21]]) as usize;
        assert_eq!(b.len(), 22 + mtrk_len);
        assert!(b.ends_with(&[0xff, 0x2f, 0x00]));
        // 2 音符 → note-on/off 各 2 个（精确匹配状态字节，避开 delta-time 字节干扰）
        let note_ons = b.iter().filter(|&&x| x == 0x90).count();
        let note_offs = b.iter().filter(|&&x| x == 0x80).count();
        assert_eq!(note_ons, 2);
        assert_eq!(note_offs, 2);
    }

    #[test]
    fn varlen_encoding() {
        let mut v = Vec::new();
        varlen(0, &mut v);
        assert_eq!(v, vec![0]);
        v.clear();
        varlen(480, &mut v);
        assert_eq!(v, vec![0x83, 0x60]);
        v.clear();
        varlen(0x0fff_ff00, &mut v);
        assert_eq!(v, vec![0xff, 0xff, 0xfe, 0x00]);
    }
}
