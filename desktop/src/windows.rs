/* The wrapper's Windows-only pieces (TODO 74): the leash, the singleton
   and the error box. */

use std::os::windows::io::AsRawHandle;
use std::process::Child;
use std::sync::atomic::{AtomicU32, Ordering};

use windows_sys::Win32::Foundation::{
    GetLastError, ERROR_ALREADY_EXISTS, HANDLE, HWND, LPARAM, LRESULT, POINT, WPARAM,
};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK,
};
use windows_sys::Win32::System::SystemInformation::GetLocalTime;
use windows_sys::Win32::System::Threading::CreateMutexW;
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::UI::Shell::{
    Shell_NotifyIconW, NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE, NOTIFYICONDATAW,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyMenu, DestroyWindow,
    DispatchMessageW, GetCursorPos, GetMessageW, LoadIconW, MessageBoxW, PostMessageW,
    PostQuitMessage, RegisterClassW, RegisterWindowMessageW, SetForegroundWindow, TrackPopupMenu,
    TranslateMessage, IDI_APPLICATION, MB_ICONERROR, MB_OK, MF_STRING, MSG, TPM_NONOTIFY,
    TPM_RETURNCMD, WM_APP, WM_CLOSE, WM_DESTROY, WM_LBUTTONUP, WM_RBUTTONUP, WNDCLASSW,
};

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(Some(0)).collect()
}

/// PR_SET_PDEATHSIG's stand-in: a Job Object that kills what it holds when
/// its last handle closes — and the only handle is this process's, which
/// Windows closes however the wrapper ends (crash and Task Manager
/// included). It holds the hub and the app window. Their own children
/// break away silently: mpv / ani-cli sessions outlive the hub as they do
/// on Linux, and a browser the hub opens for a link is the user's, not
/// the app's.
pub struct Leash(HANDLE);

impl Leash {
    pub fn new() -> Option<Leash> {
        unsafe {
            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if job.is_null() {
                return None;
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags =
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK;
            let ok = SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const std::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            (ok != 0).then_some(Leash(job))
        }
    }

    /// Put a just-spawned child on the leash. Best effort: a child that
    /// can't be added still runs (kill_hub still ends it on a clean close).
    pub fn hold(&self, child: &Child) {
        unsafe {
            AssignProcessToJobObject(self.0, child.as_raw_handle() as HANDLE);
        }
    }
}

/// True for the first wrapper of this Windows session on this port (another
/// port is another app — see wrapper_profile). The mutex handle is never
/// closed, so it lives exactly as long as this process (like flock).
pub fn first_instance(port: u16) -> bool {
    unsafe {
        let name = wide(&format!("Local\\AnimeHubDesktop-{port}"));
        let handle = CreateMutexW(std::ptr::null(), 0, name.as_ptr());
        !handle.is_null() && GetLastError() != ERROR_ALREADY_EXISTS
    }
}

/// Local wall-clock time for the logs: "2026-10-25 22:05:26.123".
pub fn now() -> String {
    let mut t: windows_sys::Win32::Foundation::SYSTEMTIME = unsafe { std::mem::zeroed() };
    unsafe { GetLocalTime(&mut t) };
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}.{:03}",
        t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond, t.wMilliseconds
    )
}

pub fn error_box(msg: &str) {
    let (text, title) = (wide(msg), wide("Anime Hub"));
    unsafe {
        MessageBoxW(std::ptr::null_mut(), text.as_ptr(), title.as_ptr(), MB_OK | MB_ICONERROR);
    }
}

/* The tray icon (Decision 51): a hidden window on its own thread, which gets
   the icon's clicks. */
const WM_TRAY: u32 = WM_APP + 1;
const OPEN: usize = 1;
const QUIT: usize = 2;
static TASKBAR_CREATED: AtomicU32 = AtomicU32::new(0);

fn icon_data(window: HWND) -> NOTIFYICONDATAW {
    let mut data: NOTIFYICONDATAW = unsafe { std::mem::zeroed() };
    data.cbSize = std::mem::size_of::<NOTIFYICONDATAW>() as u32;
    data.hWnd = window;
    data.uID = 1;
    data
}

fn add_icon(window: HWND) {
    let mut data = icon_data(window);
    data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    data.uCallbackMessage = WM_TRAY;
    unsafe {
        // Id 1: build.rs's icon group. An exe built without windres has none.
        data.hIcon = LoadIconW(GetModuleHandleW(std::ptr::null()), 1 as *const u16);
        if data.hIcon.is_null() {
            data.hIcon = LoadIconW(std::ptr::null_mut(), IDI_APPLICATION);
        }
        for (slot, unit) in data.szTip.iter_mut().zip("Anime Hub server is running".encode_utf16()) {
            *slot = unit;
        }
        Shell_NotifyIconW(NIM_ADD, &data);
    }
}

fn show_menu(window: HWND) {
    unsafe {
        let menu = CreatePopupMenu();
        AppendMenuW(menu, MF_STRING, OPEN, wide("Open Anime Hub").as_ptr());
        AppendMenuW(menu, MF_STRING, QUIT, wide("Quit").as_ptr());
        let mut at: POINT = std::mem::zeroed();
        GetCursorPos(&mut at);
        // Without this the menu stays open when you click elsewhere.
        SetForegroundWindow(window);
        let picked = TrackPopupMenu(menu, TPM_RETURNCMD | TPM_NONOTIFY, at.x, at.y, 0, window, std::ptr::null());
        DestroyMenu(menu);
        match picked as usize {
            OPEN => crate::open_window(),
            QUIT => crate::QUIT.store(true, Ordering::Relaxed),
            _ => {}
        }
    }
}

unsafe extern "system" fn tray_proc(window: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    match msg {
        WM_TRAY => match lparam as u32 {
            WM_LBUTTONUP => crate::open_window(),
            WM_RBUTTONUP => show_menu(window),
            _ => {}
        },
        WM_CLOSE => {
            Shell_NotifyIconW(NIM_DELETE, &icon_data(window));
            DestroyWindow(window);
        }
        WM_DESTROY => PostQuitMessage(0),
        // Explorer started after us, or restarted: the icon has to be added again.
        _ if msg != 0 && msg == TASKBAR_CREATED.load(Ordering::Relaxed) => add_icon(window),
        _ => return DefWindowProcW(window, msg, wparam, lparam),
    }
    0
}

pub struct Tray {
    window: isize,
    thread: Option<std::thread::JoinHandle<()>>,
}

pub fn show_tray() -> Option<Tray> {
    let (send, receive) = std::sync::mpsc::channel();
    let thread = std::thread::spawn(move || unsafe {
        let name = wide("AnimeHubTray");
        TASKBAR_CREATED.store(RegisterWindowMessageW(wide("TaskbarCreated").as_ptr()), Ordering::Relaxed);
        let mut class: WNDCLASSW = std::mem::zeroed();
        class.lpfnWndProc = Some(tray_proc);
        class.hInstance = GetModuleHandleW(std::ptr::null());
        class.lpszClassName = name.as_ptr();
        RegisterClassW(&class);
        let window = CreateWindowExW(0, name.as_ptr(), name.as_ptr(), 0, 0, 0, 0, 0,
                                     std::ptr::null_mut(), std::ptr::null_mut(), class.hInstance, std::ptr::null());
        let _ = send.send(window as isize);
        if window.is_null() {
            return;
        }
        add_icon(window);
        let mut msg: MSG = std::mem::zeroed();
        while GetMessageW(&mut msg, std::ptr::null_mut(), 0, 0) > 0 {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    });
    let window = receive.recv().ok().filter(|w| *w != 0)?;
    Some(Tray { window, thread: Some(thread) })
}

impl Drop for Tray {
    fn drop(&mut self) {
        unsafe { PostMessageW(self.window as HWND, WM_CLOSE, 0, 0) };
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}
