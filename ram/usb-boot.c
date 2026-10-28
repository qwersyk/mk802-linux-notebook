/* Fixed-image one-shot RAM Linux bootstrap; no flash/erase/reset opcodes. */
#include <libusb.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <CommonCrypto/CommonDigest.h>
#ifndef IMAGE_COUNT
#define IMAGE_COUNT 4
#endif
struct image_def {const char *name;uint32_t address;size_t size;unsigned char sha[32];};
#include "linux-images.h"
static unsigned char *images[IMAGE_COUNT];
static size_t sent[IMAGE_COUNT];
static int verified[IMAGE_COUNT],overlaid,overlay_verified,executed,storage_allowed=0;
static double last_keepalive;
static libusb_device_handle *handle;
static unsigned char ep_in,ep_out;
static uint32_t tag=0x4d4b8000;
static void fail(const char *s){fprintf(stderr,"STOP: %s; no retry/reset attempted\n",s);if(handle)libusb_close(handle);exit(1);}
static void be32(unsigned char *p,uint32_t n){for(int i=0;i<4;i++)p[i]=(n>>(24-i*8))&255;}
/* Match legacy rkflashtool send_cmd: zero DataTransferLength; tag is opaque. */
static size_t padded(size_t n){return (n+511U)&~511U;}
static int complete(void){for(int i=0;i<IMAGE_COUNT;i++)if(!verified[i])return 0;return 1;}
static void make_cbw(unsigned char *p,unsigned char op,uint32_t physical,int n,uint32_t t){
 if(op!=0&&op!=0x14&&op!=0x17&&op!=0x18&&op!=0x19&&op!=0x1b)fail("Opcode forbidden");
 if(overlaid&&op!=0x17&&op!=0x19)fail("Only RAM-read/one execute after overlay");
 memset(p,0,31);memcpy(p,"USBC",4);be32(p+4,t);p[12]=(op==0x18||op==0x19)?0:0x80;p[14]=(op==0x17||op==0x18||op==0x19||op==0x14)?10:6;p[15]=op;
 if(op==0x14){if(!storage_allowed||physical||n!=512)fail("Only verified LBA0 keepalive before overlay/ATAG permitted");p[23]=1;}
 if(op==0x18){
  int ok=0;
  if(physical==0x60081e00U&&n==256&&complete())ok=1;
  for(int i=0;i<IMAGE_COUNT;i++)if(!overlaid&&n==512&&sent[i]<padded(definitions[i].size)&&physical==definitions[i].address+sent[i])ok=1;
  if(!ok)fail("Write outside fixed sequential images or fixed overlay");
 }
 if(op==0x17){
  int ok=(physical>=0x60081c00U&&physical+128U<=0x60082a00U)||(physical>=0x60040000U&&physical+128U<=0x60044000U)||(physical==0x60099e38U);
  for(int i=0;i<IMAGE_COUNT;i++)if(physical>=definitions[i].address&&physical+128U<=definitions[i].address+padded(definitions[i].size)+512U)ok=1;
  if(n!=128||!ok)fail("Read outside fixed image/preflight ranges");
 }
 if(op==0x19){if(!overlaid||!overlay_verified||!complete()||executed||physical!=0x61f00000U||n)fail("Execute requires every verified image and overlay");executed=1;}
 if(op==0x17||op==0x18||op==0x19){be32(p+17,physical-0x60000000U);p[22]=(n>>8)&255;p[23]=n&255;}
}
static void hexlog(const char *label,const unsigned char *b,int n){fprintf(stderr,"%s[%d]=",label,n);for(int i=0;i<n;i++)fprintf(stderr,"%02x",b[i]);fputc('\n',stderr);}
static void check_csw(const unsigned char *p,int n,const unsigned char *cbw){
 hexlog("CSW",p,n);
 if(n!=13||memcmp(p,"USBS",4)||memcmp(p+4,cbw+4,4))fail("Malformed CSW or tag mismatch");
 if(p[12]){fprintf(stderr,"Device command status=%u\n",p[12]);fail("Device rejected command");}
 /* Residue is vendor metadata (e.g. readiness/version); not a universal byte count. */
}
static int request(unsigned char op,uint32_t physical,unsigned char *b,int n,int variable){
 unsigned char cbw[31],csw[13];int got=0,r,count=0;make_cbw(cbw,op,physical,n,++tag);hexlog("CBW",cbw,31);
 r=libusb_bulk_transfer(handle,ep_out,cbw,31,&got,5000);
 if(r||got!=31){fprintf(stderr,"OUT error=%s transferred=%d\n",libusb_error_name(r),got);fail("Command transfer failed");}
 if(op==0x18){
  r=libusb_bulk_transfer(handle,ep_out,b,n,&got,5000);if(r||got!=n)fail("RAM data OUT failed");count=got;
  if(physical==0x60081e00U)overlaid=1;else for(int i=0;i<IMAGE_COUNT;i++)if(physical==definitions[i].address+sent[i]){sent[i]+=(size_t)n;break;}
 }else if(n){
  r=libusb_bulk_transfer(handle,ep_in,b,n,&got,5000);
  fprintf(stderr,"DATA op=%02x error=%s transferred=%d requested=%d\n",op,libusb_error_name(r),got,n);
  if(got==13&&!memcmp(b,"USBS",4)&&!memcmp(b+4,cbw+4,4)){check_csw(b,got,cbw);fail("CSW arrived without requested data");}
  if(r||got<1||(!variable&&got!=n))fail("Data transfer failed/short read");count=got;
 }
 r=libusb_bulk_transfer(handle,ep_in,csw,13,&got,5000);
 if(r){fprintf(stderr,"STATUS error=%s transferred=%d\n",libusb_error_name(r),got);fail("Status transfer failed");}
 check_csw(csw,got,cbw);return count;
}


static double now(void){struct timespec t;if(clock_gettime(CLOCK_MONOTONIC,&t))fail("Clock unavailable");return t.tv_sec+t.tv_nsec/1e9;}
static void keepalive(void){
 if(storage_allowed&&!overlaid&&now()-last_keepalive>40.0){unsigned char sector[512];request(0x14,0,sector,512,0);if(memcmp(sector,expected_lba0,512))fail("LBA0 differs from backup");last_keepalive=now();}
}
static void read_range(uint32_t at,unsigned char *out,int n){if(n%128)fail("Bad observation size");for(int i=0;i<n;i+=128){keepalive();request(0x17,at+i,out+i,128,0);}}
static void save(const char *prefix,const char *suffix,const unsigned char *b,size_t n){char path[4096];if(snprintf(path,sizeof path,"%s.%s.bin",prefix,suffix)>=(int)sizeof path)fail("Output path too long");FILE *f=fopen(path,"wbx");if(!f)fail("Cannot create new observation");if(fwrite(b,1,n,f)!=n||fclose(f))fail("Cannot save observation");}
static void load_image(int i,const char *path){
 const struct image_def *d=&definitions[i];FILE *f=fopen(path,"rb");if(!f)fail("Cannot read image");
 images[i]=calloc(1,padded(d->size));if(!images[i])fail("Cannot allocate image");
 if(fread(images[i],1,d->size,f)!=d->size||fgetc(f)!=EOF||fclose(f))fail("Image length differs");
 unsigned char sha[32];CC_SHA256(images[i],(CC_LONG)d->size,sha);if(memcmp(sha,d->sha,32))fail("Image SHA256 differs from compiled plan; no USB opened");
}
int main(int argc,char **argv){
 if(argc!=IMAGE_COUNT+3||strcmp(argv[2],"--one-ram-linux"))fail("Usage: NEW_PREFIX --one-ram-linux GUARDED_IMAGE INITRAMFS ATAGS HANDOFF; fixed hashes, no NAND write");
 /* Paths are flexible; content/size/address are compiled and cannot be supplied by the caller. */
 for(int i=0;i<IMAGE_COUNT;i++)load_image(i,argv[3+i]);
 char lockpath[4096];if(snprintf(lockpath,sizeof lockpath,"%s.lock",argv[1])>=(int)sizeof lockpath)fail("Path too long");FILE *lock=fopen(lockpath,"wbx");if(!lock)fail("Run prefix already exists");fclose(lock);
 if(libusb_init(NULL))fail("libusb init");handle=libusb_open_device_with_vid_pid(NULL,0x2207,0x300a);if(!handle)fail("USB 2207:300a absent");
 struct libusb_device_descriptor d;if(libusb_get_device_descriptor(libusb_get_device(handle),&d))fail("USB descriptor read failed");
 if(d.bcdUSB!=0x0201)fail("Expected legacy Loader descriptor 0201");
 struct libusb_config_descriptor *cfg;if(libusb_get_active_config_descriptor(libusb_get_device(handle),&cfg))fail("USB configuration absent");int iface=-1;
 for(int i=0;i<cfg->bNumInterfaces;i++)for(int a=0;a<cfg->interface[i].num_altsetting;a++){
  const struct libusb_interface_descriptor *s=&cfg->interface[i].altsetting[a];
  if(s->bAlternateSetting||s->bInterfaceClass!=255||s->bInterfaceSubClass!=6||s->bInterfaceProtocol!=5)continue;
  iface=s->bInterfaceNumber;for(int j=0;j<s->bNumEndpoints;j++){const struct libusb_endpoint_descriptor *e=&s->endpoint[j];if((e->bmAttributes&3)!=LIBUSB_TRANSFER_TYPE_BULK)continue;if(e->bEndpointAddress&128)ep_in=e->bEndpointAddress;else ep_out=e->bEndpointAddress;}
 }
 libusb_free_config_descriptor(cfg);if(iface<0||!ep_in||!ep_out||libusb_claim_interface(handle,iface))fail("Cannot claim correct bulk interface");
 fprintf(stderr,"device_address=%u endpoints=%02x/%02x\n",libusb_get_device_address(libusb_get_device(handle)),ep_out,ep_in);


 unsigned char data[16384],state[128];request(0,0,data,0,0);struct timespec delay={0,20000000};nanosleep(&delay,NULL);
 request(0x1b,0,data,16,0);if(memcmp(data,expected_chip,16))fail("Chip identity differs");
 last_keepalive=now();
 read_range(0x60081c00,data,512);if(memcmp(data,native_csw,512))fail("Native CSW code differs");
 read_range(0x60082100,data,2304);if(memcmp(data,native_usb,2304))fail("Native USB code differs");
 read_range(0x60081e00,data,768);save(argv[1],"native-before",data,768);if(memcmp(data,native_patch_and_tail,768))fail("Native overlay region differs");
 read_range(0x60040000,data,16384);save(argv[1],"mmu-before",data,16384);if(memcmp(data,expected_table,16384))fail("MMU table differs from measured device");
 request(0x17,0x60099e38,state,128,0);save(argv[1],"loader-state-before",state,128);if(state[1]||state[2]||state[12]||state[13]||state[14]||state[15])fail("Pending format/reset/execute flag");
 storage_allowed=1;last_keepalive=now()-41;keepalive();
 puts("Preflight passed. Uploading fixed guarded kernel/rootfs/handoff/ATAGs into RAM, with complete readback.");fflush(stdout);
 /* Large buffers first; stop storage keepalive before replacing the unused low-RAM shadow with ATAGs. */
#if IMAGE_COUNT == 5
 const int order[5]={0,1,3,4,2};
#else
 const int order[4]={0,1,3,2};
#endif
 for(int slot=0;slot<IMAGE_COUNT;slot++){
  int i=order[slot];const struct image_def *d=&definitions[i];size_t n=padded(d->size);
  if(i==2 || (IMAGE_COUNT==5 && i==4)){keepalive();storage_allowed=0;}
  for(size_t off=0;off<n;off+=512){keepalive();request(0x18,d->address+(uint32_t)off,images[i]+off,512,0);}
  for(size_t off=0;off<n;off+=128){keepalive();request(0x17,d->address+(uint32_t)off,data,128,0);if(memcmp(data,images[i]+off,128))fail("RAM image readback differs; no execute");}
  verified[i]=1;printf("VERIFIED %s: %zu bytes, SHA256 pinned at build.\n",d->name,d->size);fflush(stdout);
 }
 request(0x18,0x60081e00,(unsigned char*)proof_overlay,256,0);read_range(0x60081e00,data,768);save(argv[1],"overlay-readback",data,768);
 if(memcmp(data,proof_overlay,256))fail("Overlay differs");for(int i=256;i<768;i++)if(data[i])fail("Overlay tail not zero");overlay_verified=1;
 puts("All images and overlay verified. Sending exactly ONE execute. No further Loader commands will follow.");fflush(stdout);
 request(0x19,0x61f00000,data,0,0);
#ifdef HANDOFF_CHECKPOINT
 unsigned char checkpoint[13],expected_tag[4];int received=0;
 int rc=libusb_bulk_transfer(handle,ep_in,checkpoint,13,&received,5000);be32(expected_tag,tag);
 if(rc||received!=13||memcmp(checkpoint,"USBS",4)||memcmp(checkpoint+4,expected_tag,4)||memcmp(checkpoint+8,"COK2",4)||checkpoint[12])fail("No valid cache-cleanup checkpoint; no more commands will be sent");
 save(argv[1],"cache-checkpoint",checkpoint,13);
 puts("COK2 RECEIVED: RAM execution, SVC transition, L1 and external L2 cleanup completed. Linux itself still unconfirmed.");
#endif
 puts("Handoff requested. Check DHCP and SSH; USB success alone is not a Linux boot test.");
 libusb_release_interface(handle,iface);libusb_close(handle);handle=NULL;libusb_exit(NULL);return 0;
}
