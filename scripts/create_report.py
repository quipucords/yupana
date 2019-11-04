import json
import os
import random
import socket
import struct
import sys
import uuid

BASE_HOST = {
            "display_name": "dhcp181-3.gsslab.rdu2.redhat.com",
            "fqdn": "dhcp181-3.gsslab.rdu2.redhat.com",
            "bios_uuid": "848F1E42-51ED-8D58-9FA4-E0B433EEC7E3",
            "ip_addresses": [
                "10.10.182.241"
            ],
            "mac_addresses": [
                "00:50:56:9e:f7:d6"
            ],
            "subscription_manager_id": "848F1E42-51ED-8D58-9FA4-E0B433EEC7E3",
            "facts": [
                {
                    "namespace": "satellite",
                    "facts": {
                        "rh_product_certs": [69],
                        "rh_products_installed": [
                            "RHEL"
                        ]
                    }
                }
            ],
            "system_profile": {
                "infrastructure_type": "virtualized",
                "architecture": "x86_64",
                "os_release": "Red Hat Enterprise Linux Server release 6.9 (Santiago)",
                "os_kernel_version": "6.9 (Santiago)",
                "number_of_cpus": 1,
                "number_of_sockets": 1,
                "cores_per_socket": 1
            }
}

BASE_METADATA = {
    "report_id": str(uuid.uuid4()),
    "host_inventory_api_version": "1.0",
    "source": "qpc",
    "source_metadata": {
        "report_platform_id": str(uuid.uuid4()),
        "report_type": "insights",
        "report_version": "0.0.0.bee462d",
        "qpc_server_report_id": 1,
        "qpc_server_version": "0.0.0.bee462d",
        "qpc_server_id": str(uuid.uuid4())
    },
    "report_slices": {}
}


class extend_report():
    def __init__(self, target_hosts):
        self.metadata = BASE_METADATA
        self.base_host = BASE_HOST
        self.target_hosts = target_hosts
        self.slice_max = 10000
        self.report_path = 'temp/reports/'
        self.fqdn_count = 0

    def create_host(self):
        host = self.base_host.copy()
        if host.get('mac_addresses', None):
            num_gen = random.randint(1,5)
            mac_addrs = []
            mac_base = "02:00:00:%02x:%02x:%02x"
            for x in range(0, num_gen):
                mac_addr = mac_base % (random.randint(0, 255),
                                    random.randint(0, 255),
                                    random.randint(0, 255))
                mac_addrs.append(mac_addr)
            host['mac_addresses'] = mac_addrs
        if host.get('ip_addresses', None):
            # IP Addresses
            num_gen = random.randint(1,5)
            ip_addrs = []
            for x in range(0, num_gen):
                ip = socket.inet_ntoa(struct.pack('>I', random.randint(1, 0xffffffff)))
                ip_addrs.append(ip)
            host['ip_addresses'] = ip_addrs
        uuids = ['bios_uuid', 'etc_machine_id', 'insights_client_id', 'subscription_manager_id']
        for uid in uuids:
            if host.get(uid, None):
                host[uid] = str(uuid.uuid4())
        fqdn = host.get('fqdn', None)
        if fqdn:
            self.fqdn_count += 1
            new_fqdn = 'host_number_%s' % self.fqdn_count
            host['fqdn'] = new_fqdn
        return host

    def create_slices(self):
        number_hosts = self.target_hosts
        if number_hosts % self.slice_max:
            number_of_slices = number_hosts // self.slice_max + 1
            hosts_per_slice = number_hosts // number_of_slices + 1
        else:
            number_of_slices = number_hosts // self.slice_max
            hosts_per_slice = number_hosts // number_of_slices
        host_count = hosts_per_slice
        number_hosts = self.target_hosts
        for i in range(0, number_hosts, hosts_per_slice):
            hosts = []
            for x in range(host_count):
                hosts.append(self.create_host())
            new_uuid = str(uuid.uuid4())
            new_file = os.path.join(self.report_path, '%s.json' % new_uuid)
            new_slice = { 'report_slice_id': new_uuid,
                          'hosts': hosts}
            open(new_file, 'w').write(json.dumps(new_slice))
            self.metadata['report_slices'][new_uuid] = {'number_hosts': len(hosts)}
            number_hosts = number_hosts - host_count
            if number_hosts < host_count:
                host_count = number_hosts
        current_hosts = 0
        for dict_num_hosts in self.metadata['report_slices'].values():
            current_hosts += dict_num_hosts['number_hosts']
        meta_path = os.path.join(self.report_path, 'metadata.json')
        open(meta_path, 'w').write(json.dumps(self.metadata, indent=4))


if __name__ == "__main__":
    try:
        args = sys.argv
        hosts_arg = list(filter(lambda x: 'hosts' in x, sys.argv))[0]
        host_num = int(hosts_arg.split('=')[1])
    except Exception as e:
        print('Inproper value passed in for hosts.')
        sys.exit(1)
    cla_obj = extend_report(target_hosts=host_num)
    cla_obj.create_slices()
