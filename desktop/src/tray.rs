/* The tray icon on Linux (Decision 51): a StatusNotifierItem, which needs no
   GTK. A desktop without a tray host just shows nothing, and the icon appears
   if a bar starts later (at login the server can be up before the bar). */

use std::sync::atomic::Ordering;

use ksni::blocking::{Handle, TrayMethods};

struct Item;

impl ksni::Tray for Item {
    fn id(&self) -> String {
        "anime-hub".into()
    }
    fn title(&self) -> String {
        "Anime Hub".into()
    }
    fn tool_tip(&self) -> ksni::ToolTip {
        ksni::ToolTip { title: "Anime Hub server is running".into(), ..Default::default() }
    }
    fn icon_pixmap(&self) -> Vec<ksni::Icon> {
        icon().into_iter().collect()
    }
    fn activate(&mut self, _x: i32, _y: i32) {
        crate::open_window();
    }
    fn menu(&self) -> Vec<ksni::MenuItem<Self>> {
        use ksni::menu::StandardItem;
        vec![
            StandardItem {
                label: "Open Anime Hub".into(),
                activate: Box::new(|_: &mut Self| crate::open_window()),
                ..Default::default()
            }
            .into(),
            StandardItem {
                label: "Quit".into(),
                activate: Box::new(|_: &mut Self| crate::QUIT.store(true, Ordering::Relaxed)),
                ..Default::default()
            }
            .into(),
        ]
    }
}

/// The app icon as the ARGB bytes a tray host wants.
fn icon() -> Option<ksni::Icon> {
    let mut decoder = png::Decoder::new(std::io::Cursor::new(&include_bytes!("../icons/32x32.png")[..]));
    decoder.set_transformations(png::Transformations::ALPHA);
    let mut reader = decoder.read_info().ok()?;
    let mut rgba = vec![0; reader.output_buffer_size()?];
    let info = reader.next_frame(&mut rgba).ok()?;
    let data = rgba[..info.buffer_size()].chunks_exact(4).flat_map(|p| [p[3], p[0], p[1], p[2]]).collect();
    Some(ksni::Icon { width: info.width as i32, height: info.height as i32, data })
}

pub struct Tray(Handle<Item>);

pub fn show() -> Option<Tray> {
    Item.assume_sni_available(true).spawn().ok().map(Tray)
}

impl Drop for Tray {
    fn drop(&mut self) {
        self.0.shutdown().wait();
    }
}
