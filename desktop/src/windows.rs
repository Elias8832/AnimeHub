/* The wrapper's Windows-only pieces (TODO 74): the leash, the singleton
   and the error box. */

use std::os::windows::io::AsRawHandle;
use std::process::Child;

use windows_sys::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS, HANDLE};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK,
};
use windows_sys::Win32::System::SystemInformation::GetLocalTime;
use windows_sys::Win32::System::Threading::CreateMutexW;
use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};

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
