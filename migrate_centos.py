#!/usr/bin/env python3
import logging
import sys
from pathlib import Path
import guestfs

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

BASES_DIR = Path(__file__).resolve().parent / "bases"

ACTIONS = [
    ["copy_in", str(BASES_DIR), "/run"],
    ["sh", "chown -R 0:0 /run/bases"],
    ["cp_a", "/run/bases/root", "/"],
    ["cp_a", "/run/bases/etc", "/"],
    ["chmod", 448, "/root"],
    ["chmod", 448, "/root/.ssh"],
    ["chmod", 420, "/etc/sysconfig/qemu-ga.scaleway"],
    ["chmod", 420, "/etc/systemd/system/qemu-guest-agent.service.d/50-scaleway.conf"],
    ["chmod", 420, "/etc/NetworkManager/conf.d/00-scaleway.conf"],
    ["chmod", 436, "/root/.ssh/instance_keys"],
    ["chmod", 493, "/etc"],
    ["chmod", 493, "/etc/sysconfig"],
    ["chmod", 493, "/etc/systemd"],
    ["chmod", 493, "/etc/systemd/system"],
    ["chmod", 493, "/etc/systemd/system/qemu-guest-agent.service.d"],
    ["chmod", 493, "/etc/NetworkManager"],
    ["chmod", 493, "/etc/NetworkManager/conf.d"],
    ["sh", 'echo "timeout 5;" > /etc/dhcp/dhclient.conf'],
    ["sh", "rm -Rf /run/bases"],
    ["sh", "rm -f /etc/ld.so.cache"],
    ["sh", ": > /etc/machine-id"],
    ["sh", "grubby --args=console=ttyS0,115200n8 --update-kernel $(grubby --default-kernel)"],
    ["sh", "systemctl set-default multi-user.target"],
    ["sh", r"sed -ri '/^net.ipv4.conf.all.arp_ignore\s*=/{s/.*/net.ipv4.conf.all.arp_ignore = 1/}' /etc/sysctl.conf"],
    ["umount", "/boot/efi"],
    ["selinux_relabel", "/etc/selinux/targeted/contexts/files/file_contexts", "/boot"],
    ["selinux_relabel", "/etc/selinux/targeted/contexts/files/file_contexts", "/"],
]


def guest_mount(g: guestfs.GuestFS, single_disk_mode: bool = True) -> None:
    """Monte les systèmes de fichiers, avec option pour gérer un seul disque."""
    roots = g.inspect_os()
    if len(roots) != 1:
        raise RuntimeError(f"Impossible de gérer plusieurs racines : {roots}")
    root = roots[0]
    
    # Obtenir la liste des devices disponibles
    available_devices = set(g.list_devices())
    logger.info(f"Devices disponibles : {available_devices}")
    
    for mountpoint, device in sorted(g.inspect_get_mountpoints(root).items()):
        # En mode single_disk, on ignore les points de montage des disques non disponibles
        if single_disk_mode:
            # Extraire le device de base (ex: /dev/sda1 -> /dev/sda)
            device_parts = device.split('/')
            if len(device_parts) >= 3:
                device_base = '/' + '/'.join(device_parts[1:3])
                device_base = device_base.rstrip('0123456789')
                
                # Vérifier si c'est un disque secondaire non disponible
                if device_base not in available_devices and any(skip_word in mountpoint.lower() 
                    for skip_word in ['backup', 'data', 'storage']):
                    logger.warning(f"Skipping mount of {device} on {mountpoint} - disk not available")
                    continue
        
        try:
            g.mount(device, mountpoint)
            logger.info(f"Monté : {device} sur {mountpoint}")
        except Exception as e:
            logger.warning(f"Impossible de monter {device} sur {mountpoint}: {e}")
            # Continue avec les autres points de montage


def fix_fstab_for_scaleway(g: guestfs.GuestFS) -> None:
    """Corrige les entrées fstab pour Scaleway (vda -> sda)"""
    try:
        fstab_content = g.cat("/etc/fstab")
        
        # Remplacer vda par sda si nécessaire
        if "/dev/vda" in fstab_content:
            fixed_content = fstab_content.replace("/dev/vda", "/dev/sda")
            
            # Backup du fstab original
            g.mv("/etc/fstab", "/etc/fstab.bak.migration")
            
            # Écrire le nouveau fstab
            g.write("/etc/fstab", fixed_content)
            
            logger.info("Fixed /etc/fstab entries from vda to sda")
        else:
            logger.info("/etc/fstab already uses sda entries")
    except Exception as e:
        logger.error(f"Erreur lors de la correction du fstab : {e}")


def fix_grub_for_scaleway(g: guestfs.GuestFS) -> None:
    """Corrige les entrées GRUB pour Scaleway (vda -> sda)"""
    try:
        # Corriger /etc/default/grub si nécessaire
        if g.exists("/etc/default/grub"):
            grub_content = g.cat("/etc/default/grub")
            if "/dev/vda" in grub_content:
                fixed_content = grub_content.replace("/dev/vda", "/dev/sda")
                g.write("/etc/default/grub", fixed_content)
                logger.info("Fixed /etc/default/grub entries from vda to sda")
        
        # Corriger grub.cfg si accessible
        grub_cfg_paths = ["/boot/grub2/grub.cfg", "/boot/grub/grub.cfg"]
        for grub_cfg in grub_cfg_paths:
            if g.exists(grub_cfg):
                grub_cfg_content = g.cat(grub_cfg)
                if "/dev/vda" in grub_cfg_content:
                    fixed_content = grub_cfg_content.replace("/dev/vda", "/dev/sda")
                    g.write(grub_cfg, fixed_content)
                    logger.info(f"Fixed {grub_cfg} entries from vda to sda")
    except Exception as e:
        logger.error(f"Erreur lors de la correction de GRUB : {e}")


def main(qcow_path: str, debug: bool = False) -> None:
    g = guestfs.GuestFS(python_return_dict=True)
    g.backend = "direct"
    g.set_trace(debug)
    g.set_verbose(debug)
    
    logger.info("Ajout du disque : %s", qcow_path)
    g.add_drive_opts(qcow_path, format="qcow2", readonly=False)
    g.set_network(True)
    g.launch()
    
    # Monter avec support single disk
    guest_mount(g, single_disk_mode=True)
    
    # Corriger fstab et grub AVANT les autres actions
    fix_fstab_for_scaleway(g)
    fix_grub_for_scaleway(g)
    
    # Exécuter les actions standard
    for action in ACTIONS:
        mname, *args = action
        if not isinstance(mname, str):
            raise TypeError(f"Entrée mal formée dans ACTIONS : {action!r}")
        
        # Skip umount /boot/efi si pas monté
        if mname == "umount" and args[0] == "/boot/efi" and not g.is_dir("/boot/efi"):
            logger.warning("Skipping umount /boot/efi - not mounted")
            continue
            
        ret = getattr(g, mname)(*args)
        if isinstance(ret, int) and ret != 0:
            raise RuntimeError(f"{mname} a renvoyé le code d'erreur {ret}")
    
    # Fermer proprement
    g.shutdown()
    g.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"Usage : {sys.argv[0]} <image.qcow2>")
    main(sys.argv[1])
