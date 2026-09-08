import { Component } from '@angular/core';

import { NetworkMap } from './network-map/network-map';

@Component({
  imports: [NetworkMap],
  selector: 'app-root',
  template: `<app-network-map />`,
})
export class App {}
