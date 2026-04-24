{pkgs}: {
  deps = [
    pkgs.chromium
    pkgs.libxkbcommon
    pkgs.xorg.libxkbfile
    pkgs.xorg.libxcb
    pkgs.xorg.libXrandr
    pkgs.xorg.libXfixes
    pkgs.xorg.libXext
    pkgs.xorg.libXdamage
    pkgs.xorg.libXcomposite
    pkgs.xorg.libX11
    pkgs.pango
    pkgs.mesa
    pkgs.libdrm
    pkgs.gtk3
    pkgs.glib
    pkgs.expat
    pkgs.dbus
    pkgs.cups
    pkgs.at-spi2-atk
    pkgs.atk
    pkgs.alsa-lib
    pkgs.nss
    pkgs.nspr
    pkgs.unzip
  ];
}
