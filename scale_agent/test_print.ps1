# Prueba de hardware Biotecnica: espera peso estable en la bascula (COM6) y lo
# imprime en la Zebra ZD421 via ZPL RAW (spooler USB001).

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class RawPrint {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
  public struct DOCINFO { [MarshalAs(UnmanagedType.LPStr)] public string pDocName;
    [MarshalAs(UnmanagedType.LPStr)] public string pOutputFile;
    [MarshalAs(UnmanagedType.LPStr)] public string pDataType; }
  [DllImport("winspool.Drv", EntryPoint="OpenPrinterA", SetLastError=true, CharSet=CharSet.Ansi)]
  public static extern bool OpenPrinter(string src, out IntPtr h, IntPtr pd);
  [DllImport("winspool.Drv", EntryPoint="ClosePrinter", SetLastError=true)]
  public static extern bool ClosePrinter(IntPtr h);
  [DllImport("winspool.Drv", EntryPoint="StartDocPrinterA", SetLastError=true, CharSet=CharSet.Ansi)]
  public static extern bool StartDocPrinter(IntPtr h, int level, ref DOCINFO di);
  [DllImport("winspool.Drv", EntryPoint="EndDocPrinter", SetLastError=true)]
  public static extern bool EndDocPrinter(IntPtr h);
  [DllImport("winspool.Drv", EntryPoint="StartPagePrinter", SetLastError=true)]
  public static extern bool StartPagePrinter(IntPtr h);
  [DllImport("winspool.Drv", EntryPoint="EndPagePrinter", SetLastError=true)]
  public static extern bool EndPagePrinter(IntPtr h);
  [DllImport("winspool.Drv", EntryPoint="WritePrinter", SetLastError=true)]
  public static extern bool WritePrinter(IntPtr h, byte[] buf, int n, out int written);
  public static bool Send(string printer, byte[] bytes) {
    IntPtr h; if(!OpenPrinter(printer, out h, IntPtr.Zero)) return false;
    DOCINFO di = new DOCINFO(); di.pDocName="Biotecnica Test"; di.pDataType="RAW";
    bool ok=false;
    if(StartDocPrinter(h,1,ref di)){ if(StartPagePrinter(h)){ int w;
      ok=WritePrinter(h,bytes,bytes.Length,out w); EndPagePrinter(h);} EndDocPrinter(h);}
    ClosePrinter(h); return ok;
  }
}
"@

$printer = "ZDesigner ZD421-203dpi ZPL"

# 1) Esperar peso estable > 0.3 kg (hasta 60s)
$w = $null
for ($i=0; $i -lt 60; $i++) {
  try { $w = Invoke-RestMethod http://127.0.0.1:8787/weight -TimeoutSec 1 } catch {}
  if ($w -and $w.connected -and $w.weight_kg -gt 0.3 -and $w.stable) {
    Write-Host "Peso estable detectado: $($w.weight_kg) kg (raw=$($w.raw))"
    break
  }
  Start-Sleep -Milliseconds 1000
}
if (-not $w) { Write-Host "No hay lectura del agente."; exit 1 }

$peso = "{0:N1}" -f $w.weight_kg
$raw  = $w.raw
$ts   = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")

# 2) Construir ZPL (203 dpi). Etiqueta de prueba ~50mm ancho.
$zpl = @"
^XA
^PW480
^LL0400
^CF0,36
^FO30,30^FDBIOTECNICA - PRUEBA HW^FS
^CF0,28
^FO30,90^FDBascula COM6 @ 9600 8N1^FS
^CF0,60
^FO30,140^FDPeso: $peso kg^FS
^CF0,24
^FO30,220^FDraw: $raw^FS
^FO30,260^FD$ts^FS
^FO30,300^BY2^BCN,80,Y,N,N^FD$peso^FS
^XZ
"@

$bytes = [System.Text.Encoding]::ASCII.GetBytes($zpl)
$ok = [RawPrint]::Send($printer, $bytes)
if ($ok) { Write-Host "ENVIADO a la Zebra OK -> Peso=$peso kg" }
else { Write-Host "FALLO al enviar a la impresora '$printer'" }
