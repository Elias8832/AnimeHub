/* Windows (TODO 74): give the .exe the app icon. The icon PNGs go into a
   .res file written here (no .rc: windres would need a C preprocessor);
   the GNU toolchain turns it into an object with binutils' windres, MSVC's
   linker takes the .res as it is. Without windres the build still works —
   the exe just has no icon. Nothing happens for other targets. */

use std::env;
use std::fs;
use std::path::PathBuf;
use std::process::Command;

const ICONS: [(&str, u8); 3] = [("icons/32x32.png", 32), ("icons/128x128.png", 128), ("icons/256x256.png", 0)];

fn main() {
    for (png, _) in ICONS {
        println!("cargo:rerun-if-changed={png}");
    }
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let out = PathBuf::from(env::var("OUT_DIR").unwrap());
    let res = out.join("anime-hub.res");
    fs::write(&res, icon_res()).unwrap();
    if env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc") {
        println!("cargo:rustc-link-arg-bins={}", res.display());
        return;
    }
    let obj = out.join("anime-hub-res.o");
    let target = if env::var("CARGO_CFG_TARGET_ARCH").as_deref() == Ok("x86") { "pe-i386" } else { "pe-x86-64" };
    let ok = Command::new(env::var("WINDRES").unwrap_or_else(|_| "windres".into()))
        .args(["-J", "res", "-O", "coff", "--target", target, "-i"])
        .arg(&res)
        .arg("-o")
        .arg(&obj)
        .status()
        .is_ok_and(|s| s.success());
    if ok {
        println!("cargo:rustc-link-arg-bins={}", obj.display());
    } else {
        println!("cargo:warning=windres not found (binutils) — the exe gets no icon");
    }
}

/// A .res with one RT_ICON per PNG (Vista+ reads PNG icons) and the
/// RT_GROUP_ICON that lists them (id 1: Explorer shows the first group).
fn icon_res() -> Vec<u8> {
    let mut res = Vec::new();
    entry(&mut res, 0, 0, 0, &[]); // a .res starts with an empty entry
    let mut group = vec![0, 0, 1, 0];
    group.extend((ICONS.len() as u16).to_le_bytes());
    for (i, (png, size)) in ICONS.iter().enumerate() {
        let data = fs::read(png).unwrap();
        let id = i as u16 + 1;
        group.extend([*size, *size, 0, 0]); // width, height (0 = 256), colours, reserved
        group.extend(1u16.to_le_bytes()); // planes
        group.extend(32u16.to_le_bytes()); // bits per pixel
        group.extend((data.len() as u32).to_le_bytes());
        group.extend(id.to_le_bytes());
        entry(&mut res, 3, id, 0x1010, &data); // RT_ICON, moveable | discardable
    }
    entry(&mut res, 14, 1, 0x1030, &group); // RT_GROUP_ICON, + pure
    res
}

fn entry(res: &mut Vec<u8>, kind: u16, name: u16, flags: u16, data: &[u8]) {
    res.extend((data.len() as u32).to_le_bytes());
    res.extend(32u32.to_le_bytes()); // header size
    res.extend([0xff, 0xff]);
    res.extend(kind.to_le_bytes());
    res.extend([0xff, 0xff]);
    res.extend(name.to_le_bytes());
    res.extend(0u32.to_le_bytes()); // data version
    res.extend(flags.to_le_bytes());
    res.extend((if kind == 0 { 0u16 } else { 0x0409 }).to_le_bytes()); // language: en-US
    res.extend(0u32.to_le_bytes()); // version
    res.extend(0u32.to_le_bytes()); // characteristics
    res.extend(data);
    while res.len() % 4 != 0 {
        res.push(0);
    }
}
