'''
Very simple tool for signing messages and transactions with trezor.
This was written using the SatoshiLab's python-trezor (trezorlib) API available here: https://github.com/trezor/python-trezor

Please see README for important information: https://github.com/Jason-Les/trezor-simple-sign/blob/master/README.md

By: Jason Les
JasonLes@gmail.com
twitter: @heyitscheet
'''

import binascii
from trezorlib.client import TrezorClient
from trezorlib.tx_api import TxApiBlockCypher
from trezorlib.transport_hid import HidTransport
import trezorlib.messages as proto_types
import itertools
import argparse
import base64

PREFIX_TO_COIN = {
    '2': 'Testnet',
    'm': 'Testnet',
    'n': 'Testnet',
    '1': 'Bitcoin',
    '3': 'Bitcoin',
}

PREFIX_TO_BIP32_START = {
    'm': "44'/1'",
    'n': "44'/1'",
    '2': "49'/1'",
    '1': "44'/0'",
    '3': "49'/0'",
}

PREFIX_TO_INPUT_SCRIPT = {
    '2': proto_types.InputScriptType.SPENDP2SHWITNESS,
    'm': proto_types.InputScriptType.SPENDADDRESS,
    'n': proto_types.InputScriptType.SPENDADDRESS,
    '1': proto_types.InputScriptType.SPENDADDRESS,
    '3': proto_types.InputScriptType.SPENDP2SHWITNESS,
}

PREFIX_TO_OUTPUT_SCRIPT = {
    '2': proto_types.OutputScriptType.PAYTOP2SHWITNESS,
    'm': proto_types.OutputScriptType.PAYTOADDRESS,
    'n': proto_types.OutputScriptType.PAYTOADDRESS,
    '1': proto_types.OutputScriptType.PAYTOADDRESS,
    '3': proto_types.OutputScriptType.PAYTOP2SHWITNESS,
}


# Take a target address as input and search the client until a matching bip32 path is found, then return it
def find_path(target_address, client, coin='Testnet'):
    prefix = target_address[0]
    base_path = PREFIX_TO_BIP32_START[prefix]
    # Searches up to 5 accounts and 100 addresses for each (including change addresses)
    for acct, addr, chng in itertools.product(range(5), range(100), range(2)):
        curr_path = base_path + "/{}'/{}/{}".format(acct, chng, addr)
        bip32_path = client.expand_path(curr_path)
        # Note that this function assumes that any address with the prefix '2' (Testnet) or '3' (bitcoin) is P2SH-segwit
        curr_addr = client.get_address(coin_name=coin, n=bip32_path, script_type=PREFIX_TO_INPUT_SCRIPT[prefix])
        if curr_addr == target_address:
            return bip32_path

    # Return None if search exhausts with no match
    return None

# Uses Blockcypher API to get script_type of UTXO in order to specify for the input of the new transaction
# Returns script_type as defined in InputScriptType
def get_input_script_type(api, txhash, index):
    utxo_json = api.fetch_json('txs', txhash)
    utxo_script_type_raw = utxo_json['outputs'][index]['script_type']
    if utxo_script_type_raw == 'pay-to-pubkey-hash':
        return proto_types.InputScriptType.SPENDADDRESS
    elif utxo_script_type_raw == 'pay-to-script-hash':
        # I think this may be not be best practice here. Should not assume a P2SH script type is P2SH-segwit
        # However, trezor doesn't have an option for P2SH addresses AFAIK
        return proto_types.InputScriptType.SPENDP2SHWITNESS
    else:
        raise ValueError('Unknown or unsupported script_type of input')


def sign(addr, msg, tx):
    # List all connected Trezors on USB
    devices = HidTransport.enumerate()

    # Check whether we found any trezor devices
    if len(devices) == 0:
        print
        'No TREZOR found'
        return

    # Use first connected device
    transport = devices[0]

    # Determine coin/address type corresponding to signing addresses
    addr_prefix = addr[0]
    coin = PREFIX_TO_COIN[addr_prefix]
    # TODO: Remove this to enable mainnet addresses. Currently temporarily disabled for safety.
    if coin == 'Bitcoin':
        raise ValueError('Mainnet addresses currently not supported for safety')

    # Creates object for manipulating trezor
    client = TrezorClient(transport)
    if coin == 'Testnet':
        TxApi= TxApiBlockCypher(coin, 'https://api.blockcypher.com/v1/btc/test3/')
        print('Making testnet api')
    if coin == 'Bitcoin':
        TxApi = TxApiBlockCypher(coin, 'https://api.blockcypher.com/v1/btc/main/')
        print("Making bitcoin api")

    # Set the api for trezor client
    client.set_tx_api(TxApi)

    # Find the bip32 path of the address we are signing a message or tx from
    found_path = find_path(target_address=addr, client=client, coin=coin)
    if found_path is None:
        raise ValueError('The address {} was not found on the connected trezor {} in search for its bip32 path'.format(addr,transport))
    else:
        print('Found bip32 path for: {} - signing from this address'.format(client.get_address(coin, found_path)))

    # Sign the specified message from the specified source address. Signature is in base64
    if msg is not None:
        print('Signing message: "{}"\nFrom address: {}'.format(msg, addr))
        res = client.sign_message(coin_name=coin, n=found_path, message=msg, script_type=PREFIX_TO_INPUT_SCRIPT[addr_prefix])
        print('Verify signing action on your trezor')
        print('Signature:', str(base64.b64encode(res.signature), 'ascii'))

    if tx is not None :
        # In this basic implementation, remember that tx data comes in the format:
        # <PREV HASH> <PREV INDEX> <DESTINATION ADDRESS> <AMOUNT> <FEE>
        prev_hash = tx[0]
        prev_index = int(tx[1])
        dest_address = tx[2]
        send_amount = int(tx[3])
        fee = int(tx[4])

        # Uses blockcypher API to get the amount (satoshi) of the UTXO. Amount is in satoshis
        utxo_amount = TxApi.get_tx(prev_hash).bin_outputs[prev_index].amount

        if send_amount + fee > utxo_amount:
            raise ValueError('UTXO amount of {} is too small for sending {} satoshi with {} satoshi fee'.format(utxo_amount, send_amount, fee))

        print('Using UTXO: {} and index {} to send {} {} coins to: {}'.format(prev_hash, prev_index, send_amount / 100000000, coin, dest_address))

        # Prepare the inputs of the transaction
        input_type = get_input_script_type(api=TxApi, txhash=prev_hash, index=prev_index)
        inputs = [
            proto_types.TxInputType(
                address_n=found_path,
                prev_hash=binascii.unhexlify(prev_hash),
                prev_index=prev_index,
                script_type=input_type,
                amount=utxo_amount # Amount is in satoshis
            ),
        ]

        # Prepare the outputs of the transaction
        outputs = [
            proto_types.TxOutputType(
                amount=send_amount,  # Amount is in satoshis
                script_type=PREFIX_TO_OUTPUT_SCRIPT[dest_address[0]],
                address=dest_address
            ),
        ]
        # Determine amount to send to change address. Amount is in satoshis
        change = utxo_amount - send_amount - fee
        # Add change output, which is change address on the bip32 path of the sending address
        if change > 0:
            change_path = found_path[:]
            change_path[3] = 1
            change_address = client.get_address(coin, change_path)

            outputs.append(proto_types.TxOutputType(
                amount=change, # Amount is in satoshis
                script_type=PREFIX_TO_OUTPUT_SCRIPT[change_address[0]],
                address=change_address
            ))
            print('Sending change amount of {} {} coins to change address: {}'.format(change / 100000000, coin, change_address))

        # All information is ready, sign transaction and print it
        print('Verify transaction on your trezor')
        (signatures, serialized_tx) = client.sign_tx(coin, inputs, outputs)
        # print('Signatures:', signatures)
        print('Signed transaction:', serialized_tx.hex())

    client.close()


def main():
    # Arguments for command-line use
    # TODO: Add handling for multiple inputs and outputs. Clean up argsparser
    parser = argparse.ArgumentParser(description='Sign a message or simple transaction with trezor')
    parser.add_argument("--addr", "-a", action='store', dest='addr',
                        help="Address to sign from", required=True)
    parser.add_argument("--msg", "-m", action='store', dest='msg',
                        help='Sign the following message (in quotes): "Message"')
    parser.add_argument("--tx", "-t", dest='tx', nargs=5,
                        help='Sign the following transaction in the format: <PREV HASH> <PREV INDEX> <DESTINATION ADDRESS> <AMOUNT> <FEE>'
                             ' Note: The amount and fee should be in satoshis and the fee is total fee')

    # Parse passed arguments
    args = parser.parse_args()
    signing_addr = args.addr
    msg = args.msg
    tx = args.tx

    if msg is None and tx is None:
        raise RuntimeError('No signing operation inputted, nothing to do')

    # Perform signing of message and/or transaction
    sign(signing_addr, msg, tx)


if __name__ == '__main__':
    main()
