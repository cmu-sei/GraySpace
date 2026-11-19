[${group}]
%{ for h in hosts ~}
${h.name} ansible_host=${h.ip} ansible_user=${try(h.user, "root")}%{ if try(h.user, "root") != "root" } ansible_become=true%{ endif }
%{ endfor ~}
