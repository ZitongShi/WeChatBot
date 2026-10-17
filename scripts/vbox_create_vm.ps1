# Create the VirtualBox VM used to run the relay (no OS installed yet).
#   powershell -ExecutionPolicy Bypass -File scripts\vbox_create_vm.ps1
# Then install Windows with scripts\vbox_install_windows.ps1.
param(
    [string]$Vm = "WeChatRelay",
    [string]$BaseFolder = "E:\VMs",
    [int]$MemoryMb = 4096,
    [int]$Cpus = 2,
    [int]$DiskMb = 65536
)
$ErrorActionPreference = "Stop"
$vbm = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
$disk = Join-Path $BaseFolder "$Vm\$Vm.vdi"

& $vbm createvm --name $Vm --ostype Windows11_64 --register --basefolder $BaseFolder
# VMSVGA, not VBoxSVGA: with Hyper-V/VBS active on the host, VBoxSVGA left the guest
# screen black after Windows setup rebooted.
& $vbm modifyvm $Vm --memory $MemoryMb --cpus $Cpus --firmware efi --tpm-type 2.0 `
    --graphicscontroller vmsvga --vram 128 --nic1 nat `
    --clipboard-mode disabled --drag-and-drop disabled `
    --audio-enabled off --usb-ohci off --usb-ehci off --usb-xhci off `
    --mouse usbtablet --keyboard ps2 --rtc-use-utc on
& $vbm modifynvram $Vm inituefivarstore
& $vbm modifynvram $Vm enrollmssignatures
& $vbm modifynvram $Vm enrollorclpk
& $vbm createmedium disk --filename $disk --size $DiskMb --format VDI
& $vbm storagectl $Vm --name SATA --add sata --controller IntelAhci --portcount 2
& $vbm storageattach $Vm --storagectl SATA --port 0 --device 0 --type hdd --medium $disk
Write-Host "Created VM $Vm in $BaseFolder"
