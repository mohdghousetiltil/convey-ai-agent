tried to run with new docs it it ended pretty bad

strictly for amounts

main 1st thing to solve
council authority amount -> Land certificate document -> see if client pay'ed amount + yet to pay amount = total annual amount 
otherwise, see for annual amount mentioned in the document, as total payable or annual fee something 
if and only if, the land certificate is missing, make a fallback to vendor file

water authority amount -> water information statement document -> "mostly this will be quarterly" calculate the values accordingly to annual rates, 
if quarterly then quarterly rates * 4
if total daily rates then total daily rates * 365 (based on leap year as well)
if annual rates the annual rates 'directly'
if and only if, the water information sheet is missing, make a fallback to vendor file

state revenue office, land tax -> if document have volume folio, and tax payable says 0, then its 0
if document is missing volume folio number then, the amount will be Land Tax = """" that value
if document have volume foilio number and tax payable is non zero, then the amount will be Land Tax = """" that value

Owner Corporation -> enhance the process, with more effective results