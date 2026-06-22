flake:
{ config, lib, pkgs, ... }:

let
  cfg = config.services.ruyfo-planner;
  inherit (lib) mkEnableOption mkIf mkOption types;

  backupScript = pkgs.writeShellScript "ruyfo-planner-backup" ''
    set -eu

    db=${lib.escapeShellArg "${cfg.dataDir}/ruyfo.db"}
    backup_dir=${lib.escapeShellArg cfg.backup.directory}

    if [ ! -f "$db" ]; then
      echo "No RUYFO planner database found at $db; skipping backup."
      exit 0
    fi

    mkdir -p "$backup_dir"
    chown ${cfg.user}:${cfg.group} "$backup_dir"
    chmod 0750 "$backup_dir"

    stamp="$(${pkgs.coreutils}/bin/date -u +%Y%m%dT%H%M%SZ)"
    dest="$backup_dir/ruyfo-$stamp.db"
    umask 0077
    ${pkgs.sqlite}/bin/sqlite3 "$db" ".backup '$dest'"
    chown ${cfg.user}:${cfg.group} "$dest"

    ${pkgs.findutils}/bin/find "$backup_dir" -name 'ruyfo-*.db' -type f -mtime +${toString cfg.backup.retentionDays} -delete
  '';
in
{
  options.services.ruyfo-planner = {
    enable = mkEnableOption "the RUYFO logistics planner web app";

    package = mkOption {
      type = types.package;
      default = flake.packages.${pkgs.system}.default;
      defaultText = "ruyfo-planner flake package";
      description = "Package providing the ruyfo-planner executable.";
    };

    user = mkOption {
      type = types.str;
      default = "ruyfo-planner";
      description = "User account that runs the planner service.";
    };

    group = mkOption {
      type = types.str;
      default = "ruyfo-planner";
      description = "Group account that owns the planner data directory.";
    };

    dataDir = mkOption {
      type = types.path;
      default = "/var/lib/ruyfo-planner";
      description = "Directory that stores the private SQLite database.";
    };

    host = mkOption {
      type = types.str;
      default = "127.0.0.1";
      description = "Address uvicorn binds to. Use a private interface when proxying from another host.";
    };

    port = mkOption {
      type = types.port;
      default = 8010;
      description = "TCP port uvicorn listens on.";
    };

    openFirewall = mkOption {
      type = types.bool;
      default = true;
      description = "Open the planner port in the NixOS firewall.";
    };

    environment = mkOption {
      type = types.attrsOf types.str;
      default = {};
      description = "Additional environment variables for the planner service.";
    };

    originSecretFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      description = ''
        Path to a file holding the shared secret that the fronting Fastly
        service stamps on origin requests as the X-Origin-Secret header. When
        set, the app rejects any request lacking it (HTTP 403) — the way to
        keep direct-to-origin bot traffic out when the host can't be firewalled
        to Fastly's IPs. Point this at a secret-manager path (agenix/sops),
        not a Nix store path. Null leaves the check disabled.
      '';
    };

    backup = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = "Enable a periodic SQLite backup timer.";
      };

      directory = mkOption {
        type = types.path;
        default = "${cfg.dataDir}/backups";
        defaultText = "\${config.services.ruyfo-planner.dataDir}/backups";
        description = "Directory where timestamped SQLite backups are written.";
      };

      onCalendar = mkOption {
        type = types.str;
        default = "daily";
        description = "systemd OnCalendar expression for the backup timer.";
      };

      retentionDays = mkOption {
        type = types.ints.positive;
        default = 30;
        description = "Delete timestamped planner DB backups older than this many days.";
      };
    };
  };

  config = mkIf cfg.enable {
    users.groups.${cfg.group} = {};
    users.users.${cfg.user} = {
      isSystemUser = true;
      group = cfg.group;
      home = cfg.dataDir;
    };

    networking.firewall.allowedTCPPorts = mkIf cfg.openFirewall [ cfg.port ];

    systemd.tmpfiles.rules = [
      "d ${cfg.dataDir} 0750 ${cfg.user} ${cfg.group} -"
    ] ++ lib.optional cfg.backup.enable
      "d ${cfg.backup.directory} 0750 ${cfg.user} ${cfg.group} -";

    systemd.services.ruyfo-planner = {
      description = "RUYFO logistics planner";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];

      environment = {
        RUYFO_DB = "${cfg.dataDir}/ruyfo.db";
      } // lib.optionalAttrs (cfg.originSecretFile != null) {
        RUYFO_ORIGIN_SECRET_FILE = toString cfg.originSecretFile;
      } // cfg.environment;

      serviceConfig = {
        User = cfg.user;
        Group = cfg.group;
        WorkingDirectory = cfg.dataDir;
        ExecStart = "${lib.getExe cfg.package} --host ${cfg.host} --port ${toString cfg.port}";
        Restart = "on-failure";
        RestartSec = "5s";

        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ReadWritePaths = [ cfg.dataDir ];
        CapabilityBoundingSet = "";
        LockPersonality = true;
        MemoryDenyWriteExecute = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
        SystemCallArchitectures = "native";
      };
    };

    systemd.services.ruyfo-planner-backup = mkIf cfg.backup.enable {
      description = "Back up the RUYFO planner SQLite database";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = backupScript;
      };
    };

    systemd.timers.ruyfo-planner-backup = mkIf cfg.backup.enable {
      description = "Periodic RUYFO planner SQLite backup";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = cfg.backup.onCalendar;
        Persistent = true;
      };
    };
  };
}
