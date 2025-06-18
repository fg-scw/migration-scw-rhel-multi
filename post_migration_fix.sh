#!/bin/bash
# Script à exécuter sur l'instance après le premier boot si nécessaire

set -euo pipefail

echo "=== Application des corrections post-migration ==="

# Corriger fstab
if grep -q "/dev/vda" /etc/fstab; then
    echo "Correction des entrées fstab..."
    sed -i.bak 's|/dev/vda|/dev/sda|g' /etc/fstab
fi

# Corriger GRUB
if [ -f /etc/default/grub ] && grep -q "/dev/vda" /etc/default/grub; then
    echo "Correction de GRUB..."
    sed -i.bak 's|/dev/vda|/dev/sda|g' /etc/default/grub
    
    if command -v grub2-mkconfig &> /dev/null; then
        grub2-mkconfig -o /boot/grub2/grub.cfg
    elif command -v grub-mkconfig &> /dev/null; then
        grub-mkconfig -o /boot/grub/grub.cfg
    fi
fi

# Reconstruire initramfs
echo "Reconstruction de l'initramfs..."
if command -v dracut &> /dev/null; then
    dracut -f --regenerate-all
elif command -v mkinitramfs &> /dev/null; then
    update-initramfs -u -k all
fi

# Vérifier les points de montage manquants dans fstab
echo "Vérification des points de montage..."
while read -r line; do
    if [[ $line =~ ^[^#]*(/dev/[^[:space:]]+)[[:space:]]+([^[:space:]]+) ]]; then
        device="${BASH_REMATCH[1]}"
        mountpoint="${BASH_REMATCH[2]}"
        
        # Si le device n'existe pas, commenter la ligne
        if [[ ! -e $device ]] && [[ $device =~ ^/dev/(sda|vda)[0-9]+ ]]; then
            echo "Device $device n'existe pas, désactivation dans fstab"
            sed -i "s|^.*$device.*$mountpoint.*|#&|" /etc/fstab
        fi
    fi
done < /etc/fstab

echo "=== Corrections appliquées. Redémarrage recommandé. ==="
