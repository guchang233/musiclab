// 防止 Windows Release 包额外出现控制台窗口
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    musiclab_app_lib::run()
}
