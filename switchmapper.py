from netmiko import Netmiko
from netmiko import ssh_exception
from getpass import getpass
import os
import re
import pandas
import logging
import time
import random
import sys
import socket

#**************************SETTINGS*****************************************************
#SETS THE MAX_ROWS IN A PANDA DATAFRAME TO UNLIMITED
pandas.set_option('display.max_rows', None)
INTERFACES_ERR_COL_DISPLAY = ['interface', 'link_status','description','duplex','speed','input_packets','input_errors','output_packets','output_errors']
INTERFACES_NOACT_COL_DISPLAY =  ['interface', 'link_status','description','duplex','speed', 'input_packets', 'output_packets']

#determine if IP of L3 device specified is reachable
is_l3_connected = False

# for validating an Ip-address
regex = '''^(25[0-5]|2[0-4][0-9]|[0-1]?[0-9][0-9]?)\.( 
            25[0-5]|2[0-4][0-9]|[0-1]?[0-9][0-9]?)\.( 
            25[0-5]|2[0-4][0-9]|[0-1]?[0-9][0-9]?)\.( 
            25[0-5]|2[0-4][0-9]|[0-1]?[0-9][0-9]?)$'''



#NETMIKO LOGGING SETTINGS
#logging.basicConfig(filename='netmiko.log', level=logging.DEBUG)
#logger = logging.getLogger("netmiko")



#**************************END SETTINGS*************************************************
#***************************FUNCTIONS***************************************************

# define our clear function 
def clear(): 
  
    # for windows 
    if name == 'nt': 
        _ = system('cls') 
  
    # for mac and linux(here, os.name is 'posix') 
    else: 
        _ = system('clear') 

# Define a function for
# validate an Ip addess
def checkIpAddr(Ip):
    # pass the regular expression
    # and the string in search() method
    if (re.search(regex, Ip)):
        return Ip
    else:
        return None



def get_multi_mac_ports(list, max_mac_count=2):
    rouge_ports = []
    for mac in list:
       if int(list.count(mac)) > max_mac_count and mac != 'CPU':
           if int(rouge_ports.count(mac)) == 0:
                rouge_ports.append(mac)
    return rouge_ports


def get_port_list_from_mac_list(list):
    port_list = []
    for mac in list:
        if 'destination_port' in mac:
            port_list.append(mac['destination_port'])
    return port_list

def get_rouge_ports(mac_list, trunk_list):
    #iterate through the trunk list and see if they are in the mac_list
    #if they are in the mac list remove the entries from the mac_list
    #return the mac_list [these will be the ports that might have a rouge switch]
    for port in trunk_list:
        if port in mac_list:
            while port in mac_list:
                try:
                    mac_list.remove(port)
                except ValueError:
                    fso.write("no more values exist for {}".format(port))
    return mac_list

def get_hostname_by_ip(ip):
    try:
        print(f"\n Performing lookup on IP {ip} \n")
        host_name = socket.gethostbyaddr(ip)
        print(f"Found {host_name}")
        return host_name
    except:
        print(f"\nIP address not found for {ip}\n")
        return [ip]

def display_rouge_port_info(port):
    output = net_conn.send_command("show mac address-table interface {}".format(port), use_textfsm=True)
    return output

def get_cdp_neighbor(port):
    output = net_conn.send_command("show cdp neighbor {}".format(port), use_textfsm=True)
    if type(output) != list:
        output = [{
            'neighbors': "None"
        }]
    return output
#*************************** END FUNCTIONS ***************************************************

def main(ip_addr, username, password, device_type, fso, net_conn, l3_device):
    #DECLARE THE LOCATION OF THE NTC TEMPLATES
    #os.environ['NET_TEXTFSM'] = 'C:\\ntc-templates\\templates'
    os.environ['NET_TEXTFSM'] = './ntc-templates/templates'

    #***************************SHOW COMMANDS***************************************************
    #
    interface_status = net_conn.send_command("show int status", use_textfsm=True)

    mac_table = net_conn.send_command("show mac address-table", use_textfsm=True)

    neighbor_devices = net_conn.send_command("show cdp neighbors detail", use_textfsm=True)

    interface_statistics = net_conn.send_command("show interfaces", use_textfsm=True)

    sh_ver = net_conn.send_command("show version", use_textfsm=True)
    if l3_device:
        arp_table = l3_device.send_command("show ip arp", use_textfsm=True)
    else:
        arp_table = {}

    #
    #***************************END SHOW COMMANDS***************************************************

    #**************************SCRIPT VARIABLES*****************************************************
    # GET THE TRUNK INTERFACES
    trunk_ports = []
    for interface_stat in interface_status:
        if interface_stat['vlan'] == 'trunk':
            trunk_ports.append(interface_stat['port'])

    # GET THE CONNECTED INTERFACES
    connected_interfaces = []
    for interface_stat in interface_status:
        if interface_stat['status'] == 'connected':
            connected_interfaces.append(interface_stat['port'])

    # GET THE NOTCONNECTED INTERFACES
    notconnected_interfaces = []
    for interface_stat in interface_status:
        if interface_stat['status'] == 'notconnect':
            notconnected_interfaces.append(interface_stat['port'])

    # GET THE DISABLED INTERFACES
    disabled_interfaces = []
    for interface_stat in interface_status:
        if interface_stat['status'] == 'disabled':
            disabled_interfaces.append(interface_stat['port'])

    # GET THE PORT CHANNEL INTERFACES
    etherchannel_interfaces = []
    for interface_stat in interface_status:
        if re.match(r'^Po\d{1,6}',interface_stat['port']) is not None:
            etherchannel_interfaces.append(interface_stat['port'])

    # EXTRACT THE PORT LIST FROM MAC_TABLE
    port_list = get_port_list_from_mac_list(mac_table)

    # GET PORTS WITH MORE THAN X MAC ADDRESSES [DEFAULT: 2]
    multi_macports = get_multi_mac_ports(port_list, 3)

    # COMPARE THE MULTI_MACPORTS TO THE TRUNK_PORTS AND REMOVE TRUNK PORTS
    #TO GET POSSIBLE ROUGE NETWORK DEVICES
    rouge_ports = get_rouge_ports(multi_macports, trunk_ports)

    # Filter on switched interfaces only
    switched_interfaces = []
    for interface_statistic in interface_statistics:
        if not (re.match(f'^Vlan\d{1,6}', interface_statistic['interface']) or re.match(r'^(FastEthernet|GigabitEthernet)\d$', interface_statistic['interface'])):
            switched_interfaces.append(interface_statistic)

    #copy the interface status list
    _interface_status = interface_status.copy()
    mac_count = 1
    int_stat_count = 1
    arp_table_count = 1
    match_status_found_count = 1

    for mac in mac_table:
        print(f"MAC Count: {mac_count} of {len(mac_table)}")
        mac_count+=1
        is_stat_found = False
        is_arp_found = False
        if 'Po' in mac['destination_port'] or 'CPU' in mac['destination_port']:
            continue
        for int_stat in interface_status:
            # print(f"Interface Status Count: {int_stat_count}")
            int_stat_count+=1
            if mac['destination_port'] == int_stat['port']:
                 int_stat['mac'] = mac['destination_address']
                 # print(f"Int_stat to MAC Match Count: {match_status_found_count}")
                 match_status_found_count+=1
                 is_stat_found = True
            arp_table_count = 1
            if (is_stat_found and is_arp_found):
                break
            if (is_stat_found):
                for arp in arp_table:
                    #print(arp_table_count)
                    # arp_table_count +=1
                    # if "Vlan" in arp['interface']:
                    #    continue
                    if mac['destination_address'] == arp['mac']:
                        # print(f"\n\n*********************************MATCH FOUND {mac['destination_address']}*****************************************\n\n")
                        int_stat['ip'] = arp['address']
                        int_stat['dns_name'] = get_hostname_by_ip(arp['address'])[0]
                        is_arp_found = True
                        break

    #**************************SCRIPT VARIABLES*****************************************************


    

    header = '<head><title>{}</title><link rel="stylesheet" type="text/css" href="../../styles.css" /></head>'.format(ip_addr)
    fso.write(header)
    #fso.write('-' * 100)
    fso.write("<table class='dataframe summary'>")
    fso.write("<tr><th colspan='2'><a name='summary' href='#'> Summary Information: {} </a></th></tr>".format(ip_addr))
    fso.write('<tr><td>Hostname</td><td>{}</td></tr>'.format(sh_ver[0]['hostname']))
    fso.write('<tr><td>Model</td><td>{}</td></tr>'.format(', '.join(sh_ver[0]['hardware'])))
    fso.write('<tr><td>Serial</td><td>{}</td></tr>'.format(', '.join(sh_ver[0]['serial'])))
    fso.write('<tr><td>Total ports</td><td>{}</td></tr>'.format(len(interface_status)))
    fso.write('<tr><td>Connected ports</td><td>{}/{}</td></tr>'.format(len(connected_interfaces), len(interface_status)))
    fso.write('<tr><td>Disconnected ports with Activity</td><td>{}/{}</td></tr>'.format(len(pandas.DataFrame(switched_interfaces).query(
        'link_status == "down" and (input_packets > "0" or output_packets > "0")')),len(interface_status)))
    fso.write('<tr><td>Disconnected ports with No Activity</td><td>{}/{}</td></tr>'.format(len( pandas.DataFrame(switched_interfaces).query('link_status == "down" and (input_packets == "0" or output_packets == "0")')),len(interface_status)))
    fso.write('<tr><td>Disabled ports</td><td>{}</td></tr>'.format(len(disabled_interfaces)))
    fso.write('<tr><td>Etherchannel ports</td><td>{}</td></tr>'.format(len(etherchannel_interfaces)))
    fso.write("</table>")
    #fso.write("-" * 100)
    #fso.write("\n" * 3)
    fso.write("<h1>CDP NEIGHBORS</h1>")
    #fso.write("-" * 200)
    #fso.write(str(print(pandas.DataFrame(neighbor_devices).sort_values(by=["destination_host"]))))
    pandas.DataFrame(neighbor_devices).sort_values(by=["capabilities","destination_host"], ascending=False).to_html(buf=fso,index=False,justify="center")
    #fso.write("-" * 200)



    #fso.write("-" * 100) 
    fso.write("<h1>Rouge Ports</h1> <a href='#summary' class='top_link'> top</a>" )
    #fso.write('\n' * 2)
    for p in rouge_ports:
        info = display_rouge_port_info(p)
        #fso.write('*' * 100)fso.write('*' * 100)
        fso.write("<h2>Port: {} </h2>".format(p))
        #fso.write('=' * 100)
        fso.write("<h3>Neighbor Info</h3>")
        #fso.write(pandas.DataFrame(get_cdp_neighbor(p)))
        pandas.DataFrame(get_cdp_neighbor(p)).to_html(buf=fso,index=False,justify="center")
        #fso.write('=' * 100)
        pandas.DataFrame(info).to_html(buf=fso,index=False,justify="center")
        #fso.write('*' * 100)
        #fso.write('\n' * 2)

    #fso.write INTERFACE INFO
    #
    #fso.write("-" * 100)
    #fso.write("\n" * 3)
    fso.write("<h1>Connected Interfaces</h1>  <a href='#summary' class='top_link'> top</a>")
    #fso.write("-" * 100)
    #fso.write(pandas.DataFrame(interface_status).query('status == "connected"').sort_values(by=["vlan", "name"]))
    pandas.DataFrame(interface_status).query('status == "connected"').sort_values(by=["vlan", "name"]).to_html(buf=fso,index=False,justify="center")
    #fso.write("-" * 100)

    #fso.write("-" * 100)
    #fso.write("\n" * 3)
    fso.write("<h1>Down  Interfaces</h1>  <a href='#summary' class='top_link'> top</a>")
    #fso.write("-" * 100)
    pandas.DataFrame(interface_status).query('status == "notconnect"').sort_values(by=["vlan", "name"]).to_html(buf=fso,index=False,justify="center")
    #fso.write("-" * 100)

    #fso.write("-" * 100)
    #fso.write("\n" * 3)
    fso.write("<h1>Disabled  Interfaces</h1>   <a href='#summary' class='top_link'> top</a>")
    #fso.write("-" * 100)
    pandas.DataFrame(interface_status).query('status == "disabled"').sort_values(by=["vlan", "name"]).to_html(buf=fso,index=False,justify="center")
    #fso.write("-" * 100)

    #fso.write("-" * 100)
    #fso.write("\n" * 3)
    fso.write("<h1>Down Interfaces with Activity</h1>")
    ####fso.write("-" * 200)
    pandas.DataFrame(interface_statistics).query(
        'link_status == "down" and (input_packets > "0" or output_packets > "0")').sort_values(by=["interface"])[
        INTERFACES_NOACT_COL_DISPLAY].to_html(buf=fso, index=False, justify="center")
    fso.write("<h1>Down Interfaces with No Activity</h1>  <a href='#summary' class='top_link'> top</a>")
    # fso.write("-" * 200)
    pandas.DataFrame(interface_statistics).query(
        'link_status == "down" and (input_packets == "0" or output_packets == "0")').sort_values(by=["interface"])[
        INTERFACES_NOACT_COL_DISPLAY].to_html(buf=fso, index=False, justify="center")
    # fso.write("-" * 200)
    #fso.write("-" * 200)
    #fso.write("\n" * 3)
    fso.write("<h1>Interfaces with Input/Output Errors</h1>  <a href='#summary' class='top_link'> top</a>")
    #fso.write("-" * 200)
    pandas.DataFrame(interface_statistics).query('(input_errors != "0" or output_errors != "0")').sort_values(by=["interface"])[INTERFACES_ERR_COL_DISPLAY].to_html(buf=fso,index=False,justify="center")
    #fso.write("-" * 200)

    fso.close()



if (len(sys.argv) > 1):
    filename = sys.argv[1]
    ip_file = sys.argv[1]
    print("Your file name is {}".format(ip_file))
else:
    raise Exception("You must enter a file name where your ip address are written.  See README for more information...")

username = input("Username: ")
password = getpass()
device_type = "cisco_ios"
site_code = input("Enter the site code: ")
net_conn_l3 = None
print("\n")
l3_device = input("Cisco L3 Switch IP: (Hit Enter if none) ")
user_l3_device = l3_device
l3_device = checkIpAddr(l3_device)
if l3_device:
    is_l3_connected = True
else:
    print(f"No valid L3 IP address specified. Skipping ARP and DNS lookups. {user_l3_device} \n")

INPUT_FILE_PATH = "./input_files/{}/{}".format(site_code,ip_file)
if not os.path.exists(INPUT_FILE_PATH):
    print("No input file exists at: {} \n".format(INPUT_FILE_PATH))
    message = "Create a text file named {} within the input_files folder and enter you IP addresses line by line in the file. \n\n".format(ip_file)
    raise Exception(message)
else:
    input_fso = open(INPUT_FILE_PATH, 'r')
    ip_addr = input_fso.readlines()

if type(ip_addr) == list:
    for ip in ip_addr:
        _ip = ip.strip()
        my_device = {
            'host':  _ip,
            'password': password,
            'username': username,
            'device_type': device_type
        }

        layer3_device = {
            'host': l3_device,
            'password': password,
            'username': username,
            'device_type': device_type
        }

        is_connected = True
        try: 
            net_conn = Netmiko(**my_device)
        except ssh_exception.NetMikoTimeoutException:
            is_connected = False
            print("There was a problem connecting to {}. Check the host and verify it is valid and reachable on the netowrk\n".format(_ip))
        if (is_l3_connected):
            try:
                net_conn_l3 = Netmiko(**layer3_device)
            except ssh_exception.NetMikoTimeoutException:
                # is_connected = False
                net_conn_l3 = None
                is_l3_connected = False
                print("There was a problem connecting to layer 3 device {}. Check the host and verify it is valid and reachable on the netowrk\n".format(l3_device))
                print("Skipping ARP and DNS lookups.\n")

        if (is_connected):
            if not os.path.exists(username):
                os.mkdir(username)
            if not os.path.exists('./{}/{}'.format(username, site_code)):
                os.mkdir('./{}/{}'.format(username,site_code))
            print(f"Connected to {_ip} \n")
            fso = open("./{}/{}/{}_{}_{}_{}_{}.html".format(username, site_code, _ip, time.localtime().tm_mon, time.localtime().tm_mday, time.localtime().tm_year, int(time.time())), 'w+')
            main(_ip, username, password, device_type, fso, net_conn, net_conn_l3)
            print("Query complete: Output results saved to ./{}/{}/{}_{}_{}_{}_{}.html \n".format(username, site_code, _ip, time.localtime().tm_mon, time.localtime().tm_mday, time.localtime().tm_year, int(time.time())))
            print("\n")
            fso.close()